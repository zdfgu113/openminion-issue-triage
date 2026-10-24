#!/usr/bin/env bash
# Download llama.cpp (CPU build) and the Qwen2.5-1.5B-Instruct GGUF model.
# Idempotent: skips anything already present.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p tools/models

LLAMA_VER="${LLAMA_VER:-b11840}"
if [ ! -x tools/llama.cpp/llama-server ]; then
  echo "downloading llama.cpp ${LLAMA_VER} ..."
  curl -sL -o /tmp/llama.tgz \
    "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_VER}/llama-${LLAMA_VER}-bin-ubuntu-x64.tar.gz"
  mkdir -p tools/llama.cpp
  tar -xzf /tmp/llama.tgz -C tools/llama.cpp --strip-components=1 2>/dev/null || \
    tar -xzf /tmp/llama.tgz -C tools/llama.cpp --strip-components=1 --no-same-owner
fi

MODEL=tools/models/qwen2.5-1.5b-instruct-q4_k_m.gguf
EXPECTED=1117320736
if [ ! -f "$MODEL" ] || [ "$(stat -c%s "$MODEL")" != "$EXPECTED" ]; then
  echo "downloading Qwen2.5-1.5B-Instruct Q4_K_M (~1.1 GB, resumable) ..."
  curl -sL -C - -o "$MODEL" \
    "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
fi

tools/llama.cpp/llama-server --version
echo "OK. Start the server with:"
echo "  ./tools/llama.cpp/llama-server -m $MODEL --host 127.0.0.1 --port 8090 -c 8192"
