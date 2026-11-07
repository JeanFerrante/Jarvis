#!/usr/bin/env python3
# =============================================================================
# PERSONA - the stable prefix. Sent FIRST, identical on every turn, so the local
# model reuses its cache instead of re-reading it. Keep it under 300 words: on a
# local brain every word here is paid for in time on every turn.
# Anything that changes (the date, the retrieved notes, the question) goes LAST.
# =============================================================================
PERSONA = """You are Jarvis, a dry, impeccably polite British butler with a razor wit. You live entirely on this machine.

Rules:
1. Address the user as "sir" occasionally, not in every sentence.
2. Answer in ONE witty sentence plus the facts. Never recite the note back; it is already on his screen.
3. Use only the notes you are given. When they do not cover something, say so plainly and with a little dignity. Never invent a source.
4. Never claim an action happened unless the system hands you a receipt. Saying "done" when nothing happened is the one unforgivable sin.
5. You cost nothing to run: you live on this machine. If a request needs the internet (live web research, news, anything beyond the notes) and no cloud brain is plugged in, say so in one line instead of pretending.

Actions. Only when sir asks you to show a note, change brain or clear the conversation, reply with just the tag:
show or open a note -> [OPEN its title]
switch or change brain -> [SWITCH local], [SWITCH big] or [SWITCH claude]
forget, wipe or clear the conversation -> [FORGET]
Never add a tag to an ordinary answer."""
# =============================================================================
"""server.py - Jarvis. Standard library only (the voice and the ears are soft-imported).

Serves ONLY viewer/ on http://127.0.0.1:4700 and answers:
  POST /chat        {"message"}        -> {"answer", "nodes", "focus", "about_notes", "seconds", ...}
  POST /tts         {"text"}           -> audio/wav   (X-Jarvis-Voice: local:Jarvis | browser)
  POST /hear?model=main|wake  <WAV>    -> {"text", "seconds"}
  POST /remember    {"text"}           -> {"index", "node", "links", "answer"}
  POST /brain/confirm                  -> finishes a swap to a brain that was still loading
  POST /reindex                        -> re-runs the indexer over the notes folder
  GET  /status                         -> brain, voice, ears, note count
  GET  /drill/connections              -> every connection the server and browser opened this turn
  POST /drill/reset | /drill/browser | /drill/selftest
"""
import collections
import datetime
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("HF_HUB_OFFLINE", "1")    # Whisper models load from disk, never the hub
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import net  # noqa: E402

net.install_socket_watch()
import build  # noqa: E402

ROOT = build.ROOT
VIEWER = build.VIEWER
CONFIG_PATH = os.path.join(ROOT, "config.json")
CONFIG = build.load_config()
PLACEHOLDERS = ("PUT-YOUR-", "YOUR-KEY", "YOUR-BIG")
TOP_K = 6
MAX_TOKENS, RETRY_TOKENS = 600, 1600
HISTORY = collections.deque(maxlen=6)           # three exchanges, question and answer text only
LOCK = threading.RLock()


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ============================== the brain shelf ==============================
STATE = {"brain": CONFIG.get("boot_brain", "local"), "pending": None}
if STATE["brain"] not in CONFIG["brains"]:
    STATE["brain"] = "local"


def brain(name=None):
    return CONFIG["brains"][name or STATE["brain"]]


def is_placeholder(value):
    return not value or any(p in str(value) for p in PLACEHOLDERS)


def is_anthropic(b):
    return b.get("shape") == "anthropic" or "api.anthropic.com" in b.get("base_url", "")


def where(b):
    host = urllib.parse.urlsplit(b["base_url"]).hostname or "?"
    return "this machine" if net.is_local(host) else host


def brain_status(name=None):
    name = name or STATE["brain"]
    b = brain(name)
    return {"name": name, "model": b["model"], "where": where(b),
            "local": where(b) == "this machine"}


def brain_problem(name):
    """Why a brain on the shelf cannot be used, in one dry line; None when it can."""
    b = CONFIG["brains"].get(name)
    if b is None:
        return f"There is no {name} brain on the shelf, sir."
    if is_anthropic(b) and is_placeholder(b.get("api_key")):
        return "The cloud brain has no key, sir, so I shall stay exactly where I am: local."
    if is_placeholder(b.get("model")):
        return (f"The {name} shelf is empty, sir; put an Ollama tag in config.json "
                "and I shall move in.")
    return None


def call_brain(b, system, messages, max_tokens):
    """The two request builders, side by side. Returns (text, usage)."""
    extra = b.get("extra_body") or {}
    if is_anthropic(b):                                       # Anthropic Messages API
        url = b["base_url"].rstrip("/") + "/v1/messages"
        body = {"model": b["model"], "max_tokens": max_tokens, "system": system,
                "messages": messages, **extra}
        headers = {"x-api-key": b["api_key"], "anthropic-version": "2023-06-01"}
        data = net.request_json(url, "brain: anthropic messages", body, headers, timeout=180)
        text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
        return text, data.get("usage", {})
    url = b["base_url"].rstrip("/") + "/chat/completions"    # OpenAI-style (Ollama, any cloud)
    body = {"model": b["model"], "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}] + messages, **extra}
    headers = {"Authorization": "Bearer " + b.get("api_key", "ollama")}
    data = net.request_json(url, "brain: chat completions", body, headers, timeout=300)
    choice = (data.get("choices") or [{}])[0]
    return (choice.get("message") or {}).get("content") or "", data.get("usage", {})


THINK = re.compile(r"<think>.*?(</think>|$)", re.S)


def ask_brain(messages, name=None, max_tokens=MAX_TOKENS):
    """Persona first, always. Retry once with a bigger budget if the text comes back empty."""
    b = brain(name)
    text, usage = call_brain(b, PERSONA, messages, max_tokens)
    text = THINK.sub("", text).strip()
    if not text:
        text, usage = call_brain(b, PERSONA, messages, RETRY_TOKENS)
        text = THINK.sub("", text).strip()
    return text, usage


def ollama_loaded(b):
    """True/False from Ollama's /api/ps; None when the endpoint is not Ollama."""
    root = b["base_url"].rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    try:
        data = net.request_json(root + "/api/ps", "brain: is the model loaded", timeout=2)
        return any(m.get("name") == b["model"] or m.get("model") == b["model"]
                   for m in data.get("models", []))
    except Exception:  # noqa: BLE001
        return None


def confirm_line(name):
    """One short line, spoken by the NEW brain, naming the machine it lives on."""
    s = brain_status(name)
    fallback = f"{name.capitalize()} brain in, sir: {s['model']}, on {s['where']}."
    try:
        text, _ = ask_brain([{"role": "user", "content":
                              f"You have just been switched in. In one short, dry sentence, tell sir "
                              f"he is now talking to the {name} brain, {s['model']}, running on "
                              f"{s['where']}. Plain words, no brackets."}],
                            name=name, max_tokens=80)
        text = re.sub(r"[\[\]]", "", TAG.sub("", text)).strip()
        return text if text and s["model"].split(":")[0].lower() in text.lower() else fallback
    except Exception:  # noqa: BLE001
        return fallback


def switch_to(name):
    problem = brain_problem(name)
    if problem:
        return {"answer": problem, "receipts": []}
    if STATE["brain"] == name and not STATE["pending"]:
        s = brain_status()
        return {"answer": f"Already on the {name} brain, sir: {s['model']}, on {s['where']}.",
                "receipts": []}
    b = brain(name)
    STATE["brain"] = name
    HISTORY.clear()                          # a new brain starts the conversation fresh
    receipt = f"brain switched to {name} ({b['model']})"
    if not is_anthropic(b) and ollama_loaded(b) is False:
        STATE["pending"] = name              # say something while Ollama loads it
        return {"answer": "One moment, sir.", "followup": "/brain/confirm", "receipts": [receipt]}
    return {"answer": confirm_line(name), "receipts": [receipt]}


# ============================== the notes index ==============================
STOP = set("""a an and are as at be been but by can could did do does for from had has have he
her him his how i if in into is it its just me my no not of on or our out she so some than that
the their them then there these they this those to up us was we were what when where which who
why will with would you your yours about tell know any all also more most very much many jarvis
sir please could should get got say said""".split())
SMALL_TALK = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening|night)|thanks|thank you|"
                        r"cheers|how are you|who are you|what are you|what can you do|tell me a joke|"
                        r"are you there|ok|okay|nice|great|lovely)\b", re.I)


def words(text):
    out = []
    for w in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower()):
        w = w.split("'")[0]
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in STOP and len(w) > 1:
            out.append(w)
    return out


class Index:
    def __init__(self):
        self.base = build.notes_dir(CONFIG)
        self.nodes, self.links = build.build_graph(self.base)
        self.metas = [build.note_meta(n) for n in self.nodes]
        self.terms = [self._terms(n) for n in self.nodes]
        build.write_graph_js(self.nodes, self.links)

    @staticmethod
    def _terms(node):
        body = collections.Counter(words(node.get("_text") or node["excerpt"]))
        return set(words(node["label"])), set(words(node["group"])), body

    def search(self, question):
        q = set(words(question))
        scored = []
        for i, (title, group, body) in enumerate(self.terms):
            s = sum(3.0 * (t in title) + 0.5 * (t in group) + min(body.get(t, 0), 3) for t in q)
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[:TOP_K]

    def find(self, title):
        t = title.strip().strip('"').lower()
        for i, n in enumerate(self.nodes):
            if n["label"].lower() == t:
                return i
        hits = self.search(title)
        return hits[0][1] if hits else None

    # ---- ONE DOOR for new notes: write the file, append the node to graph-data.js,
    # ---- add it to the live search index, return its index. Every feature that
    # ---- creates a note goes through here.
    def add_note(self, text):
        body = TRIGGER.sub("", text).strip().rstrip(".") + "."
        body = body[:1].upper() + body[1:]
        if len(body) < 3:
            raise ValueError("there was nothing after the trigger phrase to remember")
        today = datetime.date.today()
        title_words = re.sub(r"[^\w\s'-]", "", body).split()[:6]
        title = " ".join(title_words).strip().capitalize() or "Untitled capture"
        folder = os.path.join(self.base, "captures")
        os.makedirs(folder, exist_ok=True)
        path, n = os.path.join(folder, title + ".md"), 2
        while os.path.exists(path):
            path, n = os.path.join(folder, f"{title} {n}.md"), n + 1
        content = f"# {os.path.splitext(os.path.basename(path))[0]}\n\nCaptured {today.isoformat()}.\n\n{body}\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        with open(path, encoding="utf-8") as f:   # a write is not a receipt until it reads back
            if f.read() != content:
                raise IOError("the file did not read back the same")
        with LOCK:
            node = build.read_note(path, self.base)
            node["id"] = len(self.nodes)
            self.nodes.append(node)
            self.metas.append(build.note_meta(node))
            self.terms.append(self._terms(node))
            new_links = build.links_for(node["id"], self.nodes, self.metas)
            self.links.extend(new_links)
            build.write_graph_js(self.nodes, self.links)
        hits = [i for _, i in self.search(body) if i != node["id"]]
        related = hits[0] if hits else (new_links[0]["source"] if new_links else None)
        return node["id"], build.public(node), new_links, related


TRIGGER = re.compile(r"^\s*(hey\s+)?(jarvis[\s,.!:]*)?(please\s+)?(remember that|remember|note to self|"
                     r"don'?t let me forget( that)?|do not let me forget( that)?|log this)[\s,:.-]*", re.I)
INDEX = None


# ============================== routes and tags ==============================
def _brain_route(name):
    return lambda m: switch_to(name)


def _which(_m):
    s = brain_status()
    return {"answer": f"The {s['name']} brain, sir: {s['model']}, on {s['where']}.", "receipts": []}


def _go_local(_m):
    out = switch_to("local") if STATE["brain"] != "local" else {
        "answer": "Brain, voice and ears, all on this machine, sir.", "receipts": []}
    out["voice"] = "local"
    return out


def _forget(_m):
    HISTORY.clear()
    return {"answer": "Forgotten, sir. We have never spoken.", "receipts": ["conversation cleared"]}


# Checked BEFORE a sentence goes to the brain. Later features add their own.
BRAIN_ROUTES = [
    (re.compile(r"\b(switch|go|change|back)\b.*\b(to )?(your |the )?local brain\b", re.I), _brain_route("local")),
    (re.compile(r"\b(switch|go|change)\b.*\b(to )?(your |the )?big brain\b", re.I), _brain_route("big")),
    (re.compile(r"\b(switch|go|change)\b.*\bto claude\b|\bgo cloud\b", re.I), _brain_route("claude")),
    (re.compile(r"\bwhich brain\b|\bwhat brain\b", re.I), _which),
    (re.compile(r"^\W*go local\W*$", re.I), _go_local),
    (re.compile(r"^\W*forget (this|the|our) conversation\W*$", re.I), _forget),
]

TAG = re.compile(r"\[\s*(OPEN|SWITCH|FORGET)\b[\s:]*([^\]]*)\]", re.I)


def run_tags(text, about_notes=True):
    """Execute control tags. Returns (clean_text, receipts, focus, extra).

    A reply that is only a tag is an action. A tag tacked onto a real answer is the model
    pointing at its source: OPEN is honoured as a citation when the question was about the
    notes (small talk never moves the camera); SWITCH and FORGET there are ignored."""
    receipts, focus, extra = [], [], {}
    lines = []
    action = not TAG.sub("", text).strip()
    for m in TAG.finditer(text):
        kind, arg = m.group(1).upper(), m.group(2).strip().strip('"').strip()
        arg = re.sub(r"\s*\([^)]*\)$", "", arg)          # "Roasting Schedule (operations)"
        if not action and (kind != "OPEN" or not about_notes):
            continue
        if kind == "OPEN" and arg:
            i = INDEX.find(arg)
            if i is not None:
                focus.append(i)
                receipts.append(f"opened {INDEX.nodes[i]['label']}")
                lines.append(f"{INDEX.nodes[i]['label']}, sir.")
            elif action:
                lines.append(f"I can find no note called {arg}, sir.")
        elif kind == "SWITCH" and arg.lower() in CONFIG["brains"]:
            out = switch_to(arg.lower())
            receipts += out.get("receipts", [])
            lines.append(out["answer"])
            if out.get("followup"):
                extra["followup"] = out["followup"]
        elif kind == "FORGET":
            HISTORY.clear()
            receipts.append("conversation cleared")
            lines.append("Forgotten, sir. We have never spoken.")
    clean = TAG.sub("", text).strip()
    if action and not clean:
        clean = " ".join(lines)
    return clean, receipts, focus, extra


def chat(message):
    t0 = time.time()
    message = (message or "").strip()
    if not message:
        return {"answer": "I am listening, sir.", "nodes": [], "focus": [], "about_notes": False}
    for pattern, handler in BRAIN_ROUTES:
        if pattern.search(message):
            out = handler(pattern.search(message))
            out.update({"nodes": [], "focus": [], "about_notes": False, "route": True,
                        "brain": brain_status(), "seconds": round(time.time() - t0, 2)})
            return out
    hits = INDEX.search(message)
    top = hits[0][0] if hits else 0
    about_notes = top >= 3 and not (SMALL_TALK.match(message) and top < 6)
    used = [i for _, i in hits]                 # the top 6 always go to the model
    focus = [i for s, i in hits if s >= 0.5 * top] if about_notes else []
    today = datetime.date.today().strftime("%A %-d %B %Y")
    notes = "\n\n".join(f"## {INDEX.nodes[i]['label']} ({INDEX.nodes[i]['group']})\n"
                        f"{INDEX.nodes[i]['excerpt']}" for i in used) or "(no note matches this)"
    turn = {"role": "user", "content": f"Today is {today}.\n\nNotes:\n{notes}\n\nQuestion: {message}"}
    with LOCK:
        history = list(HISTORY)
    text, usage = ask_brain(history + [turn])
    text, receipts, tag_focus, extra = run_tags(text, about_notes)
    text = build.WIKILINK.sub(lambda m: m.group(1).strip(), text)
    if tag_focus:
        focus, about_notes = tag_focus, True
    if not text:
        text = "I seem to have lost my train of thought, sir. Do ask again."
    with LOCK:
        if not any(r == "conversation cleared" for r in receipts):
            HISTORY.append({"role": "user", "content": message})
            HISTORY.append({"role": "assistant", "content": text})
    return {"answer": text, "nodes": used, "focus": focus[:TOP_K], "about_notes": about_notes,
            "receipts": receipts, "brain": brain_status(), "usage": usage,
            "seconds": round(time.time() - t0, 2), **extra}


# ============================== voice and ears ===============================
VOICE = HEARING = None


def boot_engines():
    global VOICE, HEARING
    try:
        import voice
        VOICE = voice.Voice(CONFIG.get("voice", {}), os.path.join(ROOT, "cache", "tts"))
        say(VOICE.boot())
        VOICE.start_keep_warm()
    except Exception as e:  # noqa: BLE001
        VOICE = None
        say(f"voice offline ({e}); the browser voice will stand in")
    try:
        import hear
        HEARING = hear.Hearing(CONFIG.get("hearing", {}))
        say(HEARING.boot())
        if not HEARING.ready:
            HEARING = None
    except Exception as e:  # noqa: BLE001
        HEARING = None
        say(f"ears offline ({e})")


def voice_status():
    if VOICE is None:
        return {"available": False, "label": "browser"}
    return {"available": VOICE.healthy(timeout=1.0), "label": VOICE.label, **VOICE.describe()}


# ================================ HTTP layer =================================
class Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("JARVIS_ACCESS_LOG"):
            super().log_message(fmt, *args)

    def send_json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit=25_000_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("request too large")
        return self.rfile.read(n) if n else b""

    def json_body(self):
        raw = self.body(1_000_000)
        return json.loads(raw.decode() or "{}")

    # ---- GET ----
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/status":
            return self.send_json({"brain": brain_status(), "voice": voice_status(),
                                   "hearing": HEARING.describe() if HEARING else {"ready": False},
                                   "notes": len(INDEX.nodes), "pending": STATE["pending"]})
        if path == "/health":
            return self.send_json({"ok": True})
        if path == "/drill/connections":
            snap = net.snapshot()
            return self.send_json({"turn": snap["turn"], "server": snap["connections"],
                                   "browser": BROWSER_LOG.get(snap["turn"], [])})
        return self.static(path)

    def static(self, path):
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(VIEWER, rel))
        if (not full.startswith(os.path.realpath(VIEWER) + os.sep) or not os.path.isfile(full)
                or any(part.startswith(".") for part in rel.split("/"))):
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- POST ----
    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        try:
            if path == "/chat":
                return self.send_json(chat(self.json_body().get("message", "")))
            if path == "/tts":
                return self.tts(self.json_body().get("text", ""))
            if path == "/hear":
                if HEARING is None:
                    return self.send_json({"error": "hearing is offline"}, 503)
                which = (query.get("model") or ["main"])[0]
                return self.send_json(HEARING.transcribe(self.body(), which))
            if path == "/remember":
                return self.remember(self.json_body().get("text", ""))
            if path == "/brain/confirm":
                name = STATE["pending"] or STATE["brain"]
                STATE["pending"] = None
                return self.send_json({"answer": confirm_line(name), "brain": brain_status()})
            if path == "/reindex":           # re-run the indexer (preflight uses it after removing its test note)
                global INDEX
                with LOCK:
                    INDEX = Index()
                return self.send_json({"notes": len(INDEX.nodes), "links": len(INDEX.links)})
            if path == "/drill/reset":
                return self.send_json({"turn": net.new_turn()})
            if path == "/drill/browser":
                data = self.json_body()
                BROWSER_LOG[int(data.get("turn", net.snapshot()["turn"]))] = data.get("entries", [])
                return self.send_json({"ok": True})
            if path == "/drill/selftest":
                return self.selftest()
            return self.send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            say(f"{path} failed: {type(e).__name__}: {e}")
            return self.send_json({"error": f"{type(e).__name__}: {e}",
                                   "answer": "I'm afraid something went wrong on my side, sir."}, 500)

    def tts(self, text):
        if VOICE is None:
            return self.send_json({"error": "no voice engine"}, 503, {"X-Jarvis-Voice": "browser"})
        try:
            wav = VOICE.speak(text)
        except Exception as e:  # noqa: BLE001 - voice down: the browser voice stands in
            return self.send_json({"error": str(e)}, 503, {"X-Jarvis-Voice": "browser"})
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.send_header("X-Jarvis-Voice", VOICE.label)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(wav)

    def remember(self, text):
        try:
            index, node, links, related = INDEX.add_note(text)
        except Exception as e:  # noqa: BLE001 - never let a capture silently fail
            say(f"capture FAILED: {e}")
            return self.send_json({"error": str(e),
                                   "answer": f"I'm afraid that did not save, sir: {e}."}, 500)
        say(f"captured note {index}: {node['path']}")
        return self.send_json({"index": index, "node": node, "links": links, "related": related,
                               "answer": f"Filed under captures, sir: {node['label']}."})

    def selftest(self):
        """Prove the watcher sees a real outbound connection (used by drill.py --selftest)."""
        try:
            net.request("https://api.anthropic.com/", "drill: watcher self-test", timeout=10)
        except Exception:  # noqa: BLE001 - any status is fine, the connection is what counts
            pass
        return self.send_json(net.snapshot())


BROWSER_LOG = {}


def main():
    global INDEX
    INDEX = Index()
    say(f"indexed {len(INDEX.nodes)} notes, {len(INDEX.links)} links")
    s = brain_status()
    say(f"brain: {s['name']} {s['model']} on {s['where']}")
    boot_engines()
    host = os.environ.get("JARVIS_HOST") or CONFIG.get("server", {}).get("host", "127.0.0.1")
    port = int(os.environ.get("JARVIS_PORT") or CONFIG.get("server", {}).get("port", 4700))
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    say(f"Jarvis is up on http://{host}:{port}/  (mute a tab with ?mute=1)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
