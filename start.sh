#!/usr/bin/env bash
# Start Jarvis on http://127.0.0.1:4700
#
# The brain must answer on http://localhost:11434/v1 (Ollama's port and API).
#   * Ollama installed?  It is used, and started if it is not running.
#   * No Ollama (a Linux box or cloud container)?  llama.cpp's OpenAI-compatible server is
#     started on the same port, under the same model tag, from the .gguf in models/.
#     setup-linux-cpu.sh installs that path.
set -euo pipefail
cd "$(dirname "$0")"

TAG=$(python3 -c 'import json; c=json.load(open("config.json")); print(c["brains"][c.get("boot_brain","local")]["model"])')
brain_up() { curl -s -m 2 http://localhost:11434/v1/models 2>/dev/null | grep -q '"data"'; }

if ! brain_up; then
  if command -v ollama >/dev/null 2>&1; then
    echo "starting Ollama ..."
    (ollama serve >/dev/null 2>&1 &)
  else
    GGUF=$(ls models/*.gguf 2>/dev/null | head -n 1 || true)
    if [ -z "$GGUF" ]; then
      echo "No Ollama and no models/*.gguf. Install Ollama (ollama.com/download) and run:  ollama pull $TAG"
      echo "or, on Linux without Ollama:  ./setup-linux-cpu.sh"
      exit 1
    fi
    echo "no Ollama here: serving $GGUF as \"$TAG\" with llama.cpp on :11434 ..."
    nohup python3 -m llama_cpp.server --model "$GGUF" --model_alias "$TAG" \
      --host 127.0.0.1 --port 11434 --n_ctx 8192 > models/llama_server.log 2>&1 &
  fi
  for _ in $(seq 1 300); do brain_up && break; sleep 1; done
  brain_up || { echo "the brain did not come up on :11434"; exit 1; }
fi
echo "brain up: $(curl -s http://localhost:11434/v1/models | python3 -c 'import sys,json; print(", ".join(m["id"] for m in json.load(sys.stdin)["data"]))')"
exec python3 server.py
