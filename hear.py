"""hear.py - the hearing bay. Whisper on this machine; soft-imported by server.py.

mlx_whisper on Apple Silicon, faster_whisper on Windows and Linux. Both models (the
LARGE one for commands, the SMALL one for the wake word) are loaded ONCE at boot, kept in
memory, and warmed with one second of silence so the first real transcription is not a
cold start. WAV bodies are decoded here with the standard library (16-bit PCM), so no
ffmpeg is involved.
"""
import io
import platform
import threading
import time
import wave

# config name -> model to load, per package
MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}
FW_SIZES = {"large-v3-turbo": "large-v3-turbo", "large-v3": "large-v3", "medium": "medium",
            "small": "small", "base": "base", "tiny": "tiny"}


def pcm_from_wav(data):
    """WAV bytes -> float32 numpy array at 16 kHz mono."""
    import numpy as np
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("expected 16-bit PCM WAV")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:  # linear resample, plenty for speech
        n = int(round(len(audio) * 16000 / rate))
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return audio.astype(np.float32)


class Hearing:
    def __init__(self, cfg):
        self.cfg = cfg
        self.main_name = cfg.get("model", "large-v3-turbo")
        self.wake_name = cfg.get("wake_model", "small")
        self.language = cfg.get("language", "en")
        self.package = None
        self.models = {}
        self.locks = {"main": threading.Lock(), "wake": threading.Lock()}

    def boot(self):
        """Load and warm both models. Returns a status line; never raises."""
        try:
            use_mlx = platform.system() == "Darwin" and platform.machine() == "arm64"
            if use_mlx:
                import mlx_whisper  # noqa: F401 soft import
                self.package = "mlx_whisper"
                self.models = {"main": MLX_REPOS.get(self.main_name, self.main_name),
                               "wake": MLX_REPOS.get(self.wake_name, self.wake_name)}
            else:
                from faster_whisper import WhisperModel  # soft import
                self.package = "faster_whisper"
                kw = {"device": "auto", "compute_type": self.cfg.get("compute_type", "int8")}
                self.models = {"main": WhisperModel(FW_SIZES.get(self.main_name, self.main_name), **kw)}
                self.models["wake"] = (self.models["main"] if self.wake_name == self.main_name else
                                       WhisperModel(FW_SIZES.get(self.wake_name, self.wake_name), **kw))
            import numpy as np
            silence = np.zeros(16000, dtype=np.float32)
            for which in ("wake", "main"):
                self._run(which, silence)
            return f"ears ready: {self.package} {self.main_name} + {self.wake_name}"
        except Exception as e:  # noqa: BLE001
            self.package, self.models = None, {}
            return f"ears offline ({type(e).__name__}: {e})"

    @property
    def ready(self):
        return bool(self.models)

    def describe(self):
        return {"package": self.package, "model": self.main_name, "wake_model": self.wake_name,
                "ready": self.ready}

    def _run(self, which, audio):
        with self.locks[which]:
            if self.package == "mlx_whisper":
                import mlx_whisper
                out = mlx_whisper.transcribe(audio, path_or_hf_repo=self.models[which],
                                             language=self.language)
                return out.get("text", "").strip()
            segments, _ = self.models[which].transcribe(
                audio, language=self.language, beam_size=1 if which == "wake" else 5,
                vad_filter=False, condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segments).strip()

    def transcribe(self, wav_bytes, which="main"):
        if not self.ready:
            raise RuntimeError("hearing is offline")
        which = "wake" if which == "wake" else "main"
        t = time.time()
        text = self._run(which, pcm_from_wav(wav_bytes))
        return {"text": text, "seconds": round(time.time() - t, 2), "model": which}


if __name__ == "__main__":
    # One-time, online: download both models named in config.json and warm them.
    # server.py always loads them with HF_HUB_OFFLINE=1, so it never phones home.
    import json
    import os
    os.environ.pop("HF_HUB_OFFLINE", None)
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
        print(Hearing(json.load(f).get("hearing", {})).boot())
