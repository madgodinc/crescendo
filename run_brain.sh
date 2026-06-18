#!/usr/bin/env bash
# Launch the Crescendo brain (mgi-mind serve-http) as an ISOLATED systemd --user
# unit: own Qdrant (:6344), own MGIMIND_HOME, per-agent bearer tokens from .env so
# every audit row is signed by a trustworthy token-derived author. Survives across
# shell exits (systemd cgroup). Does NOT touch Mad's personal ~/mgimind data.
#
# CRITICAL: ORT_DYLIB_PATH must be passed into the unit — systemd-run starts with a
# clean env, and without it the ONNX session deadlocks (see mind procedure
# 64ec27fa). The interactive shell has it; the daemon does not inherit it.
cd "$(dirname "$0")" || exit 1
# shellcheck disable=SC1091
source .env

ORT="${ORT_DYLIB_PATH:-/home/madgodinc/mgimind/onnxruntime/libonnxruntime.so}"
HOME_ISO="$PWD/workspace/.mgimind"

systemctl --user stop crescendo-brain 2>/dev/null
systemctl --user reset-failed crescendo-brain 2>/dev/null
sleep 1

systemd-run --user --unit=crescendo-brain \
  -p StandardOutput=journal -p StandardError=journal \
  --setenv=MGIMIND_HOME="$HOME_ISO" \
  --setenv=ORT_DYLIB_PATH="$ORT" \
  --setenv=PATH="/home/madgodinc/.cargo/bin:/usr/local/bin:/usr/bin:/bin" \
  "$PWD/brain/target/release/mgimind" serve-http --host 127.0.0.1 --port 8765 \
  --agent-token "conductor:${MGIMIND_TOKEN_CONDUCTOR}" \
  --agent-token "soloist:${MGIMIND_TOKEN_SOLOIST}" \
  --agent-token "tuning_fork:${MGIMIND_TOKEN_TUNING_FORK}" \
  --agent-token "stage_tech:${MGIMIND_TOKEN_STAGE_TECH}" \
  --agent-token "archivist:${MGIMIND_TOKEN_ARCHIVIST}"

echo "[brain] systemd unit launched; waiting for health..."
for _ in $(seq 1 30); do
  if [ "$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/health 2>/dev/null)" = "200" ]; then
    echo "[brain] health OK on http://127.0.0.1:8765"; exit 0
  fi
  sleep 2
done
echo "[brain] WARNING: health not up after 60s"; exit 1
