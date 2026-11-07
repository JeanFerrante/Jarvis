# Jarvis, fully local

A 3D galaxy of your markdown notes, with a dry British butler who answers from them in a
local voice and listens through a local Whisper. No API keys, no subscriptions, and nothing
leaves the machine: `drill.py` proves it.

Built from the two prompt packs *Build Jarvis Fully Offline* and *Run Jarvis Fully Local*
(prompts 01 to 08), all in one pass.

```
notes/  ──build.py──▶  viewer/graph-data.js ──▶  viewer/index.html (galaxy, voice player, mic)
                                                    │  /chat /tts /hear /remember
                                              server.py  :4700, 127.0.0.1 only
                              ┌────────────────────┼────────────────────┐
                         brain (Ollama API)     voice.py              hear.py
                         localhost:11434/v1     VoiceStudio / Piper   mlx- / faster-whisper
```

## Run it

```bash
./start.sh
```

Then open <http://127.0.0.1:4700/>. Type a question, or click **TALK** (push-to-talk) or
**HANDS-FREE** (say "Jarvis …"). Add `?mute=1` to any tab that must never speak.

Prove it works, every time something changes:

```bash
python3 preflight.py
```

```bash
python3 drill.py
```

### On this Linux cloud container (no Ollama, no VoiceStudio, CPU only)

GitHub release downloads are blocked here, so Ollama could not be installed. `start.sh`
instead serves the model with **llama.cpp's OpenAI-compatible server on Ollama's port
(11434) under an Ollama-style tag**, so Jarvis's code is the same either way. The voice is
**Piper** (`en_GB-alan-medium`, a local British voice, not a clone), and the ears are
**faster-whisper** `small` + `base`. `./setup-linux-cpu.sh` installs all of it.

Measured here (4 CPU cores, no GPU): **hear 1.7 s, think 16.8 s, speak 0.2 s to the first byte**.
Thinking is slow because a 3B model reads about 1,100 prompt tokens on a CPU. On an
Apple Silicon Mac with Ollama it is a few seconds.

### On Windows (one double-click)

1. Download this repository (green **Code** button → **Download ZIP**) and unzip it, for
   example into `Documents\Jarvis`.
2. Double-click **`INSTALAR-JARVIS.bat`**. It installs Python and Ollama with `winget` if
   they are missing, pulls the brain, installs Whisper and the Piper voice, starts Jarvis,
   opens it in your browser and runs the preflight. Run it again any time; it only fetches
   what is missing.
3. Afterwards, **`LIGAR-JARVIS.bat`** starts it again without touching the internet.
   To turn Jarvis off, close its minimised Python window.

### On your Mac (the prompt-pack setup)

1. `ollama pull qwen3.5:9b` (or the brain for your memory size, see the packs).
2. VoiceStudio: install CSM 1B on the MLX engine, then clone a profile named `Jarvis`.
3. `python3 -m pip install mlx-whisper`, then run `python3 hear.py` once to download the models.
4. Edit `config.json`:
   - `brains.local.model` → your Ollama tag
   - `voice.backend` → `"voicestudio"` (engine `mlx-audio`, model `mlx-community/csm-1b-8bit`)
   - `hearing` → `{"model": "large-v3-turbo", "wake_model": "small"}`
5. `./start.sh`, then `python3 preflight.py`.

On Windows with an NVIDIA card, use VoiceStudio's `voxcpm2` engine and `faster-whisper`.

## What's in it

| File | What it does |
|---|---|
| `build.py` | The indexer: every `.md` becomes a node (id equals its index); links when a note mentions another's title, links to it with `[[wikilinks]]`, or shares two or more wikilink targets with it. |
| `server.py` | The persona block at the top, the brain shelf, retrieval, routes, control tags, and the HTTP API. Serves only `viewer/`. |
| `voice.py` | VoiceStudio (pins the engine with `/engines/select`, finds the `Jarvis` profile) or Piper. WAV cache, 600-character cap, keeps the engine warm every 10 idle minutes. |
| `hear.py` | Whisper, both models loaded once and warmed. Decodes 16 kHz WAV itself. Loads offline (`HF_HUB_OFFLINE=1`). |
| `net.py` | The watcher: every outbound connection, logged with host and reason, plus a socket-level safety net. |
| `viewer/index.html` | The galaxy, sentence-chunked voice player with prefetch, the ONE `play()` function, the mic, wake word, finish window, interrupt and the badge. |
| `viewer/vendor/` | 3d-force-graph 1.80.1, pinned and on disk. See `VERSIONS.txt`. |
| `preflight.py` | 13 live checks and the latency budget. |
| `drill.py` | The Wi-Fi-off drill and the honest list. |
| `notes/` | 31 sample notes about a small coffee roastery. Point `notes_dir` in `config.json` at your own. |

## Things to say

- "When do we roast for wholesale?", "Who delivers to the wholesale accounts?"
- "Remember that the finish window should be 900 milliseconds" (also "note to self", "log this")
- "Which brain are you on?", "Switch to your big brain", "Switch to Claude", "Go local"
- "Use the browser voice", "Use your local voice", "Stop" / "Wait" (while he talks)
- "What's the weather in Lisbon?" (he will tell you, in one line, that he can't)

## The brain shelf

`config.json` → `brains` holds `local`, `big` and `claude`. `big` ships with a placeholder
tag and `claude` with the placeholder key `PUT-YOUR-KEY-HERE`; while they are placeholders,
Jarvis declines the swap in one dry line and stays local. Type a real key into
`config.json` yourself, never into a chat. Swaps last until a restart, which boots back to
`boot_brain`.

## Tuning

At the top of `viewer/index.html`: `SILENCE_MS` (700, how you pause) and `FINISH_MS` (900,
how you think). Tune them after a day of real use, and one at a time.

## Honest notes

- `drill.py --cloud` must FAIL. With no key it proves the watcher with one deliberate call
  to `api.anthropic.com`, made only on that request.
- With the Piper voice, Jarvis speaks in a stock local voice, not a clone of yours.
  Cloning needs VoiceStudio. Clone only a voice you have permission to use.
- The small Whisper model sometimes mishears words ("milk" became "mill" in testing with a
  synthetic voice). The large model on a Mac does much better.
