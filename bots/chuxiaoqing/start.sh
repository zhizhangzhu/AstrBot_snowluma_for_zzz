#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ "${1:-}" != "--qq-only" ]] && [[ "$(docker inspect -f '{{.State.Running}}' xiaoxiaoqing-astrbot 2>/dev/null || true)" != true ]]; then
  exec ../../start.sh
fi
docker compose --env-file .env up -d --no-deps snowluma
printf '统一管理：http://10.74.4.16:6185\n初小晴QQ登录：http://10.74.4.16:6082/vnc.html?autoconnect=1&resize=scale\n'
