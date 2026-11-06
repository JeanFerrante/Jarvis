"""voice.py - the voice bay. Standard library only; soft-imported by server.py.

Two local backends behind one interface:
  * "voicestudio" (default): the VoiceStudio app on http://127.0.0.1:3900 with a cloned
    profile. At boot, and once per engine run, the engine is pinned with
    POST /engines/select so the app's default (non-commercial) pack never sneaks in.
  * "piper": Piper TTS, a small local engine for Linux machines with no VoiceStudio.
    Soft-imported; only used when config.json says "backend": "piper".

Every rendered WAV is cached on disk by a hash of text + profile, so repeated lines
(the greeting, "Standing by.") are instant. One request is capped at 600 characters.
"""
import hashlib
import io
import json
import os
import threading
import time
import wave

import net

MAX_CHARS = 600
WARM_EVERY = 10 * 60          # seconds of idle before "Standing by." keeps the model loaded


class VoiceDown(Exception):
    pass


class Voice:
    def __init__(self, cfg, cache_dir):
        self.cfg = cfg
        self.backend = cfg.get("backend", "voicestudio")
        self.url = cfg.get("url", "http://127.0.0.1:3900").rstrip("/")
        self.engine = cfg.get("engine", "mlx-audio")
        self.model_id = cfg.get("model_id", "mlx-community/csm-1b-8bit")
        self.profile = cfg.get("profile", "Jarvis")
        self.language = cfg.get("language", "en")
        self.profile_id = None
        self.pinned_run = None       # VoiceStudio engine run we pinned, to re-pin after a restart
        self.cache_dir = cache_dir
        self.last_used = time.time()
        self._piper = None
        self._lock = threading.Lock()
        os.makedirs(cache_dir, exist_ok=True)

    # ---- identity -------------------------------------------------------------
    @property
    def label(self):
        return f"local:{self.profile}"

    def describe(self):
        engine = "piper " + os.path.basename(self.cfg.get("piper_model", "")) \
            if self.backend == "piper" else f"{self.engine} {self.model_id}"
        return {"backend": self.backend, "profile": self.profile, "engine": engine}

    # ---- boot -----------------------------------------------------------------
    def boot(self):
        """Pin the engine and find the profile. Returns a short status line; never raises."""
        try:
            if self.backend == "piper":
                return self._boot_piper()
            return self._boot_voicestudio()
        except Exception as e:  # noqa: BLE001 - boot must never take the server down
            return f"voice offline ({e}); the browser voice will stand in"

    def _boot_piper(self):
        from piper import PiperVoice  # soft import
        path = self.cfg.get("piper_model", "")
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        if not os.path.isfile(path):
            raise VoiceDown(f"piper model not found: {path}")
        self._piper = PiperVoice.load(path)
        return f"voice ready: piper, profile {self.profile}"

    def _boot_voicestudio(self):
        health = net.request_json(self.url + "/health", "voice: health at boot", timeout=2)
        run = None
        if isinstance(health, dict):
            run = health.get("run_id") or health.get("pid") or health.get("started_at")
        net.request_json(self.url + "/engines/select", "voice: pin engine", timeout=30, payload={
            "family": "tts", "backend_id": self.engine, "model_id": self.model_id})
        self.pinned_run = run or "pinned"
        profiles = net.request_json(self.url + "/profiles", "voice: find profile", timeout=5)
        if isinstance(profiles, dict):
            profiles = profiles.get("profiles") or profiles.get("data") or []
        for p in profiles or []:
            if str(p.get("name", "")).lower() == self.profile.lower():
                self.profile_id = p.get("id") or p.get("profile_id")
        if not self.profile_id:
            raise VoiceDown(f'no VoiceStudio profile named "{self.profile}"')
        return f"voice ready: {self.engine} {self.model_id}, profile {self.profile}"

    # ---- health ---------------------------------------------------------------
    def healthy(self, timeout=1.0):
        if self.backend == "piper":
            return self._piper is not None
        try:
            status, _, body = net.request(self.url + "/health", "voice: health", timeout=timeout)
            if status != 200:
                return False
            try:  # if the app restarted, its engine run changed: pin again
                h = json.loads(body.decode() or "{}")
                run = h.get("run_id") or h.get("pid") or h.get("started_at")
                if run and run != self.pinned_run:
                    self.boot()
            except ValueError:
                pass
            return self.profile_id is not None or self.boot().startswith("voice ready")
        except Exception:  # noqa: BLE001
            return False

    # ---- speech ---------------------------------------------------------------
    def _cache_path(self, text):
        key = hashlib.sha256(f"{self.backend}|{self.engine}|{self.model_id}|{self.profile}|{text}"
                             .encode()).hexdigest()[:32]
        return os.path.join(self.cache_dir, key + ".wav")

    def speak(self, text, use_cache=True):
        """Return WAV bytes for text. Raises VoiceDown when the engine is not there."""
        text = " ".join(text.split())[:MAX_CHARS]
        if not text:
            raise ValueError("empty text")
        self.last_used = time.time()
        path = self._cache_path(text)
        if use_cache and os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
        if not self.healthy(timeout=1.0):
            raise VoiceDown("voice engine did not answer within one second")
        with self._lock:
            wav = self._render_piper(text) if self.backend == "piper" else self._render_vs(text)
        if use_cache:
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(wav)
            os.replace(tmp, path)
        return wav

    def _render_vs(self, text):
        status, _, body = net.request(self.url + "/v1/audio/speech", "voice: render speech",
                                      timeout=120, data={
                                          "model": self.model_id, "input": text,
                                          "voice": self.profile_id, "response_format": "wav",
                                          "language": self.language})
        if status != 200 or body[:4] != b"RIFF":
            raise VoiceDown(f"VoiceStudio returned {status}, not a WAV")
        return body

    def _render_piper(self, text):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self._piper.synthesize_wav(text, w)
        return buf.getvalue()

    # ---- keep it warm ---------------------------------------------------------
    def start_keep_warm(self):
        def loop():
            while True:
                time.sleep(30)
                if time.time() - self.last_used >= WARM_EVERY:
                    try:
                        self.speak("Standing by.", use_cache=False)   # audio thrown away
                    except Exception:  # noqa: BLE001
                        pass
                    self.last_used = time.time()
        threading.Thread(target=loop, name="voice-keep-warm", daemon=True).start()
