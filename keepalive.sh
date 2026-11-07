#!/usr/bin/env bash
# Keep Jarvis up until you stop this script: restarts the brain and the server if either dies.
#   JARVIS_HOST=0.0.0.0 ./keepalive.sh    # also reachable from a preview/proxy in front of this machine
cd "$(dirname "$0")"
mkdir -p cache
while true; do
  if ! curl -s -m 3 http://127.0.0.1:4700/health >/dev/null; then
    echo "$(date '+%F %T') Jarvis is down: starting it"
    pkill -f "python3 server.py" 2>/dev/null
    nohup ./start.sh >> cache/jarvis.log 2>&1 &
    for _ in $(seq 1 200); do curl -s -m 2 http://127.0.0.1:4700/health >/dev/null && break; sleep 3; done
    echo "$(date '+%F %T') $(curl -s -m 3 http://127.0.0.1:4700/health || echo 'still down')"
  fi
  sleep 15
done
