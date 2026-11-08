#!/usr/bin/env python3
"""preflight.py - runs every LIVE chain end to end against the running Jarvis and prints a verdict.

Not unit tests, not mocks: real calls, because the failures that hurt are the ones where every
unit test passes and a real chain is dead. Start the server first (./start.sh), then:

    python3 preflight.py

Ends with two lines: the LATENCY BUDGET measured in this run, and the summary.
Exit code is non-zero on any FAIL. Voice and ears missing are WARN (the browser voice stands in).
"""
import difflib
import glob
import hashlib
import http.client as httpclient
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VIEWER = os.path.join(ROOT, "viewer")
CONFIG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
SERVER = "http://{host}:{port}".format(**{"host": "127.0.0.1", "port": 4700, **CONFIG.get("server", {})})
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # localhost never goes via a proxy
LOOP_SENTENCE = "The roaster runs on Monday, Wednesday and Friday mornings."
CDN_HOSTS = ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "esm.sh", "cdn.skypack.dev",
             "ajax.googleapis.com", "fonts.googleapis.com", "fonts.gstatic.com", "code.jquery.com",
             "cdn.tailwindcss.com", "jspm.io")

results = []                 # (state, name, detail)
budget = {}
TICK, CROSS, WARN = "✔", "✘", "!"


def check(name, fn, warn_only=False):
    try:
        detail = fn()
        results.append(("PASS", name, detail or ""))
        print(f"  {TICK} {name}" + (f"  ({detail})" if detail else ""))
    except Exception as e:  # noqa: BLE001
        state = "WARN" if warn_only else "FAIL"
        results.append((state, name, str(e)))
        print(f"  {WARN if warn_only else CROSS} {name}  [{state}] {e}")


def http(method, path, body=None, ctype="application/json", timeout=300, base=SERVER):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": ctype} if data is not None else {})
    try:
        with DIRECT.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jget(path, **kw):
    status, _, body = http("GET", path, **kw)
    return status, json.loads(body.decode() or "null")


def jpost(path, body=None, **kw):
    status, _, raw = http("POST", path, body if body is not None else {}, **kw)
    return status, json.loads(raw.decode() or "null")


# ----------------------------------------------------------------------------- checks
def server_up():
    status, headers, body = http("GET", "/")
    assert status == 200 and b"<title>Jarvis</title>" in body, f"GET / returned {status}"
    return SERVER


GRAPH = {}


def graph_loads():
    status, _, body = http("GET", "/graph-data.js")
    assert status == 200, f"graph-data.js returned {status}"
    text = body.decode()
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    assert len(data["nodes"]) > 0, "zero nodes"
    assert all(n["id"] == i for i, n in enumerate(data["nodes"])), "a node id is not its index"
    GRAPH.update(data)
    return f"{len(data['nodes'])} nodes, {len(data['links'])} links"


def ollama_has_brain():
    b = CONFIG["brains"]["local"]           # always checked: it is the fallback and "go local"
    _, models = jget("/models", base=b["base_url"].rstrip("/"), timeout=5)
    ids = [m.get("id") for m in models.get("data", [])]
    if b["model"] not in ids:
        raise AssertionError(f"{b['model']} is not pulled. Run:  ollama pull {b['model']}   (have: {ids})")
    return f"{b['model']} at {b['base_url']}"


def chat_answers():
    q = f"What does the {GRAPH['nodes'][0]['label']} note say?" if GRAPH else "When do we roast?"
    t = time.time()
    status, out = jpost("/chat", {"message": q})
    budget["think"] = time.time() - t
    assert status == 200, f"/chat returned {status}: {out}"
    assert isinstance(out.get("answer"), str) and out["answer"].strip(), "empty answer"
    assert isinstance(out.get("nodes"), list) and out["nodes"], "no nodes array"
    assert isinstance(out.get("seconds"), (int, float)), "no seconds"
    return f"{out['seconds']}s, nodes {out['nodes'][:6]}: {out['answer'][:70]}"


def ttfb(path, body):
    conn = httpclient.HTTPConnection("127.0.0.1", SERVER.rsplit(":", 1)[1], timeout=300)
    t = time.time()
    conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    r = conn.getresponse()
    first = r.read(1)
    seconds = time.time() - t
    rest = r.read()
    headers = dict(r.getheaders())
    conn.close()
    return r.status, headers, first + rest, seconds


def voice_works():
    _, st = jget("/status")
    v = st["voice"]
    assert v.get("available"), "voice engine offline; the browser voice is standing in"
    cfg = CONFIG.get("voice", {})
    if v.get("backend", "voicestudio") == "voicestudio":
        status, _, _ = http("GET", "/health", base=cfg.get("url", "http://127.0.0.1:3900"), timeout=2)
        assert status == 200, f"VoiceStudio /health returned {status}"
        assert v["engine"] == f"{cfg.get('engine')} {cfg.get('model_id')}", \
            f"engine {v['engine']} does not match config.json"
    else:
        assert os.path.basename(cfg.get("piper_model", "")) in v["engine"], "piper model does not match config.json"
    fresh = f"Preflight voice check, {time.strftime('%H %M %S')}."      # uncached on purpose
    status, headers, wav, seconds = ttfb("/tts", {"text": fresh})
    assert status == 200 and wav[:4] == b"RIFF", f"/tts returned {status}, not a WAV"
    budget["speak"] = seconds
    return f"{v['label']} via {v['engine']}, {len(wav) // 1024} KB WAV, first byte {seconds:.2f}s"


def words(t):
    return re.findall(r"[a-z]+", t.lower())


def loop_agrees():
    status, headers, wav, _ = ttfb("/tts", {"text": LOOP_SENTENCE})
    assert status == 200, "voice offline, the loop cannot run"
    status, _, raw = http("POST", "/hear?model=main", wav, ctype="audio/wav")
    assert status == 200, f"/hear returned {status}: {raw[:120]!r}"
    out = json.loads(raw)
    budget["hear"] = out["seconds"]
    ratio = difflib.SequenceMatcher(None, words(LOOP_SENTENCE), words(out["text"])).ratio()
    assert ratio >= 0.8, f"heard “{out['text']}” ({ratio:.0%} match)"
    return f"heard “{out['text']}” in {out['seconds']}s, {ratio:.0%} match"


def remember_round_trip():
    code = str(int(time.time()) % 9000 + 1000)
    status, out = jpost("/remember", {"text": f"remember that the preflight zebra code is {code}"})
    assert status == 200, f"/remember returned {status}: {out}"
    path = os.path.join(ROOT, CONFIG.get("notes_dir", "notes"), out["node"]["path"])
    try:
        assert os.path.isfile(path), f"no file at {path}"
        status, ans = jpost("/chat", {"message": "What is the preflight zebra code?"})
        assert out["index"] in ans.get("nodes", []), f"/chat did not retrieve note {out['index']}"
        assert code in ans["answer"], f"answer did not carry the code {code}: {ans['answer']}"
        return f"note {out['index']} written and retrieved: {ans['answer'][:60]}"
    finally:
        if os.path.isfile(path):
            os.remove(path)
        jpost("/reindex")
        jpost("/chat", {"message": "forget this conversation"})


def served_matches_disk():
    files = [p for p in glob.glob(os.path.join(VIEWER, "**", "*"), recursive=True) if os.path.isfile(p)]
    bad = []
    for p in files:
        rel = os.path.relpath(p, VIEWER).replace(os.sep, "/")
        status, _, body = http("GET", "/" + urllib.request.quote(rel))
        if status != 200 or hashlib.sha256(body).digest() != hashlib.sha256(open(p, "rb").read()).digest():
            bad.append(rel)
    assert not bad, f"served differs from disk: {bad}"
    return f"{len(files)} files byte-identical"


def config_hidden():
    leaks = []
    for path in ("/config.json", "/../config.json", "/%2e%2e/config.json", "/..%2fconfig.json",
                 "//config.json", "/viewer/../config.json", "/server.py", "/notes/operations/Roasting%20Schedule.md"):
        status, _, body = http("GET", path)
        if status == 200 or b'"brains"' in body or b"PERSONA" in body:
            leaks.append(f"{path} -> {status}")
    if leaks:
        raise AssertionError("!!! CONFIG OR SOURCE IS REACHABLE FROM THE BROWSER: " + ", ".join(leaks) + " !!!")
    return "config.json, sources and notes all 404"


def mute_is_silent():
    html = open(os.path.join(VIEWER, "index.html"), encoding="utf-8").read()
    body = html[html.index("function play(item)"):]
    body = body[: body.index("\nasync function speak")]
    assert re.search(r"function play\(item\) \{\s*if \(MUTED\)", body), "play() does not check MUTED first"
    outside = html.replace(body, "")
    for sink in ("new Audio(", "speechSynthesis.speak(", ".play()"):
        assert sink not in outside, f"{sink} appears outside play(): something can route around the mute"
    try:                                          # and prove it in a real tab when a browser is here
        out = subprocess.run(["node", "-e", MUTE_JS, SERVER + "/?mute=1"], capture_output=True,
                             text=True, timeout=60, cwd=ROOT)
        if out.returncode == 0 and out.stdout.strip().startswith("{"):
            m = json.loads(out.stdout.strip().splitlines()[-1])
            assert m["playsStarted"] == 0 and m["playRefusals"] >= 1, f"muted tab played audio: {m}"
            return f"play() refuses first; live ?mute=1 tab: {m['playRefusals']} refused, 0 played"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "play() is the only sink and refuses first (static check; no headless browser here)"


MUTE_JS = r"""
const { chromium } = require('playwright');
(async () => { const b = await chromium.launch(); const p = await b.newPage();
  await p.goto(process.argv[1]); await p.waitForTimeout(3500);
  console.log(JSON.stringify(await p.evaluate(() => JARVIS.metrics))); await b.close(); })()
  .catch(e => { console.error(e); process.exit(1); });
"""


def no_cloud_key():
    secrets = [b.get("api_key") for b in CONFIG["brains"].values() if b.get("api_key") not in (None, "", "ollama")]
    patterns = [re.escape(s) for s in secrets] + [r"sk-ant-[A-Za-z0-9_-]{10,}", r"PUT-YOUR-KEY-HERE"]
    hits = []
    for p in glob.glob(os.path.join(VIEWER, "**", "*"), recursive=True):
        if os.path.isfile(p):
            text = open(p, encoding="utf-8", errors="ignore").read()
            hits += [f"{os.path.relpath(p, ROOT)}" for pat in patterns if re.search(pat, text)]
    assert not hits, f"key material under viewer/: {sorted(set(hits))}"
    return "no key, real or placeholder, under viewer/"


def no_cdn():
    hits = []
    for p in glob.glob(os.path.join(VIEWER, "**", "*"), recursive=True):
        if not os.path.isfile(p) or p.endswith("VERSIONS.txt"):
            continue
        text = open(p, encoding="utf-8", errors="ignore").read()
        hits += [f"{os.path.relpath(p, ROOT)}: {h}" for h in CDN_HOSTS if h in text]
        if p.endswith(".html"):
            hits += [f"{os.path.relpath(p, ROOT)}: {m}" for m in
                     re.findall(r"""(?:src|href)\s*=\s*["'](https?://[^"']+)""", text)]
    assert not hits, f"CDN references: {hits}"
    return "every script and style loads from viewer/"


def drill_passes():
    _, st = jget("/status")
    if not st["brain"]["local"]:
        raise AssertionError(f"the {st['brain']['name']} brain is live, so answers leave this machine "
                             f"({st['brain']['where']}); say \"go local\" and run drill.py to prove local mode")
    import drill
    ok, hosts, _ = drill.run(quiet=True, browser=False)
    assert ok, f"outbound hosts during a turn: {hosts}"
    return "a full turn opened no connection beyond this machine"


def main():
    print(f"Jarvis preflight  ·  {SERVER}  ·  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    check("server is up and serving the viewer", server_up)
    if results[-1][0] == "FAIL":
        print("\n  Start it first:  ./start.sh")
        return finish()
    check("graph data loads, node count > 0", graph_loads)
    check("brain is pulled and answering on /v1/models", ollama_has_brain)
    check("/chat answers a real question with nodes and seconds", chat_answers)
    check("voice: engine up, pinned to config, /tts returns a WAV", voice_works, warn_only=True)
    check("THE LOOP: /tts -> /hear gives the same words back", loop_agrees, warn_only=True)
    check("/remember writes a file that /chat retrieves at once", remember_round_trip)
    check("files the browser is served match the files on disk", served_matches_disk)
    check("config.json is NOT reachable from the browser", config_hidden)
    check("a ?mute=1 tab is really silent", mute_is_silent)
    check("no cloud key anywhere under viewer/", no_cloud_key)
    check("no CDN reference anywhere under viewer/", no_cdn)
    check("the Wi-Fi-off drill passes in local mode", drill_passes, warn_only=True)
    return finish()


def finish():
    h, t, s = budget.get("hear"), budget.get("think"), budget.get("speak")
    fmt = lambda x: f"{x:.1f} s" if x is not None else "n/a"
    total = sum(x for x in (h, t, s) if x is not None)
    n = {k: sum(1 for r in results if r[0] == k) for k in ("PASS", "FAIL", "WARN")}
    print()
    print(f"LATENCY BUDGET: hear {fmt(h)}, think {fmt(t)}, speak {fmt(s)}, total {total:.1f} s (measured in this run)")
    print(f"SUMMARY: {n['PASS']} pass, {n['FAIL']} fail, {n['WARN']} warn")
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):        # a Windows console may not speak UTF-8
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
