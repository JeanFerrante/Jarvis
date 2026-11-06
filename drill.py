#!/usr/bin/env python3
"""drill.py - the Wi-Fi-off drill. Proves a conversation with Jarvis never leaves this machine.

    python3 drill.py             # local mode: must PASS with an empty host list
    python3 drill.py --cloud     # "go cloud": must FAIL and name api.anthropic.com
    python3 drill.py --no-browser

One fresh turn: a real question through /chat, the answer spoken through /tts, that WAV fed
back through /hear. Then GET /drill/connections and PRINT every host that is not localhost or
127.0.0.1, from the server's watcher and from the browser's resource timing (a headless
?mute=1 tab runs its own turn when Playwright is installed). Passes only when the list is empty.
Ends with THE HONEST LIST: the features that still need the internet, read from the code.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse

import preflight as pf

ROOT = pf.ROOT
QUESTION = "Which days does the café's milk get delivered, and by whom?"
LOCAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


def host_of(url):
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""


def outbound(conns):
    """Hosts that are not this machine, from a /drill/connections reply."""
    hosts = {c["host"]: c["reason"] for c in conns.get("server", []) if not c.get("local")}
    for e in conns.get("browser", []):
        h = host_of(e.get("url", ""))
        if e.get("url", "").startswith(("blob:", "data:")) or h in LOCAL:
            continue
        hosts[h] = f"browser {e.get('type', 'resource')}"
    return hosts


BROWSER_JS = r"""
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage(); const urls = [];
  p.on('request', r => urls.push(r.url()));
  await p.goto(process.argv[1]); await p.waitForTimeout(1500);
  await p.fill('#q', process.argv[2]); await p.press('#q', 'Enter');
  await p.waitForFunction(() => JARVIS.metrics.speeches.length >= 2 && JARVIS.metrics.speeches[1].done != null, null, { timeout: 240000 });
  await p.waitForTimeout(800);
  const res = await p.evaluate(() => performance.getEntriesByType('resource').map(e => ({ url: e.name, type: e.initiatorType })));
  console.log(JSON.stringify({ turn: await p.evaluate(() => JARVIS.turn), requests: urls, resources: res }));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(1); });
"""


def browser_turn(say):
    """A real ?mute=1 tab runs a turn; returns {host: reason} for everything it fetched."""
    try:
        out = subprocess.run(["node", "-e", BROWSER_JS, pf.SERVER + "/?mute=1", QUESTION],
                             capture_output=True, text=True, timeout=300, cwd=ROOT)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        say(f"  (browser turn skipped: {e})")
        return None
    if out.returncode != 0:
        say(f"  (browser turn skipped: {out.stderr.strip()[:160]})")
        return None
    data = json.loads(out.stdout.strip().splitlines()[-1])
    _, conns = pf.jget("/drill/connections")
    hosts = outbound(conns)
    for u in data["requests"] + [r["url"] for r in data["resources"]]:
        h = host_of(u)
        if h not in LOCAL and not u.startswith(("blob:", "data:")):
            hosts[h] = "browser request"
    say(f"  browser turn {data['turn']}: {len(data['requests'])} requests, "
        f"{len(conns.get('server', []))} server connections")
    return hosts


def script_turn(say):
    _, t = pf.jpost("/drill/reset")
    status, ans = pf.jpost("/chat", {"message": QUESTION})
    assert status == 200, f"/chat failed: {ans}"
    say(f"  turn {t['turn']}: /chat  {ans['seconds']}s  “{ans['answer'][:80]}”")
    status, _, wav, sec = pf.ttfb("/tts", {"text": ans["answer"][:600]})
    if status == 200:
        say(f"           /tts   {sec:.2f}s to first byte, {len(wav) // 1024} KB")
        status, _, raw = pf.http("POST", "/hear?model=main", wav, ctype="audio/wav")
        if status == 200:
            say(f"           /hear  {json.loads(raw)['seconds']}s  “{json.loads(raw)['text'][:70]}”")
        else:
            say(f"           /hear  offline ({status})")
    else:
        say(f"           /tts   offline ({status}): the browser voice would stand in")
    _, conns = pf.jget("/drill/connections")
    return outbound(conns), conns


def honest_list():
    """Features that still need the internet, read from the code and config, not from memory."""
    items = []
    for name, b in pf.CONFIG["brains"].items():
        h = host_of(b["base_url"])
        if h not in LOCAL:
            keyed = not any(x in str(b.get("api_key", "")) for x in ("PUT-YOUR-", "YOUR-KEY"))
            items.append(f"the {name} brain ({b['model']} at {h}): "
                         + ("key set, used only after you say “switch to Claude” / “go cloud”"
                            if keyed else "no key, unplugged"))
    code = {}
    for f in ["server.py", "voice.py", "hear.py", "net.py", "build.py", "viewer/index.html"]:
        code[f] = open(os.path.join(ROOT, f), encoding="utf-8").read()
    for f, text in code.items():
        for i, line in enumerate(text.splitlines(), 1):
            for url in re.findall(r"""https?://[^\s"'<>)]+""", line):
                h = host_of(url)
                if (h and h not in LOCAL and "{" not in h and "watcher self-test" not in line
                        and not line.lstrip().startswith(("#", "//", "*"))):
                    items.append(f"{f}:{i} reaches {h}")
    if "drill: watcher self-test" in code["server.py"]:
        items.append("server.py /drill/selftest opens api.anthropic.com, only when drill.py --cloud asks, "
                     "to prove the watcher sees it")
    if 'os.environ.pop("HF_HUB_OFFLINE"' in code["hear.py"]:
        items.append("hear.py run by hand (python3 hear.py) downloads the Whisper models once; "
                     "the server loads them with HF_HUB_OFFLINE=1")
    if not re.search(r"web.?search|/research|duckduckgo|serpapi|bing\.com", "".join(code.values()), re.I):
        items.append("live web research: not built; Jarvis says so in one line instead of pretending")
    if "speechSynthesis" in code["viewer/index.html"] and "localService" in code["viewer/index.html"]:
        items.append("(not internet) the browser-voice fallback uses only voices with localService")
    return items


def run(quiet=False, browser=True, cloud=False):
    say = (lambda *a: None) if quiet else print
    say(f"Wi-Fi-off drill  ·  {'CLOUD' if cloud else 'local'} mode  ·  {pf.SERVER}\n")
    selftest = False
    if cloud:
        _, out = pf.jpost("/chat", {"message": "go cloud"})
        say(f"  go cloud -> “{out['answer']}”")
        if out.get("brain", {}).get("local", True):          # the swap was refused: no key
            selftest = True
            say("  the cloud brain has no key, so the drill proves the watcher with a deliberate call\n")
    hosts = {}
    if browser and not cloud:
        b = browser_turn(say)
        if b is not None:
            hosts.update(b)
    if selftest:
        pf.jpost("/drill/reset")
        _, snap = pf.jpost("/drill/selftest")
        hosts.update(outbound({"server": snap["connections"]}))
    else:
        h, _ = script_turn(say)
        hosts.update(h)
    if cloud:
        pf.jpost("/chat", {"message": "switch to your local brain"})
    say("\nHOSTS THAT ARE NOT THIS MACHINE:")
    if hosts:
        for h, why in sorted(hosts.items()):
            say(f"  {h}   ({why})")
    else:
        say("  (none)")
    ok = not hosts
    say(f"\nDRILL: {'PASS - nothing left this machine' if ok else 'FAIL - ' + ', '.join(sorted(hosts))}")
    items = honest_list()
    say("\nTHE HONEST LIST - features that still need the internet:")
    for it in items:
        say(f"  - {it}")
    return ok, sorted(hosts), items


if __name__ == "__main__":
    args = sys.argv[1:]
    ok, _, _ = run(browser="--no-browser" not in args, cloud="--cloud" in args)
    sys.exit(0 if ok else 1)
