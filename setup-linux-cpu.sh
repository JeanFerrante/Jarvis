#!/usr/bin/env bash
# One-time setup for a Linux machine WITHOUT Ollama or VoiceStudio (for example a cloud
# container). Idempotent: run it again and it only fetches what is missing.
#
#   brain : llama.cpp server (llama-cpp-python) + Qwen2.5 3B Instruct, Q4_K_M (~2 GB)
#   voice : Piper + the en_GB "alan" voice (~60 MB)   - a local voice, not a clone
#   ears  : faster-whisper "small" + "base"            - downloaded once, then loaded offline
#
# On a Mac, follow the prompt packs instead: Ollama, VoiceStudio and mlx-whisper.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p models/piper

python3 -c 'import llama_cpp.server' 2>/dev/null || python3 -m pip install --quiet 'llama-cpp-python[server]'
python3 -c 'import faster_whisper, piper' 2>/dev/null || python3 -m pip install --quiet faster-whisper piper-tts

HF=https://huggingface.co
[ -s models/qwen2.5-3b-instruct-q4_k_m.gguf ] || curl -fsSL -o models/qwen2.5-3b-instruct-q4_k_m.gguf \
  "$HF/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
for f in en_GB-alan-medium.onnx en_GB-alan-medium.onnx.json; do
  [ -s "models/piper/$f" ] || curl -fsSL -o "models/piper/$f" \
    "$HF/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/$f"
done
python3 hear.py      # downloads and warms the Whisper models named in config.json
echo "setup done. Start Jarvis with ./start.sh"
