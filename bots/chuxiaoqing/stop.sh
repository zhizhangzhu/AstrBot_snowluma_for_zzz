#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ "${1:-}" == "--legacy-astrbot" ]]; then
  docker update --restart=no chuxiaoqing-astrbot >/dev/null
  docker compose --env-file .env --profile standalone-legacy stop astrbot
else
  docker compose --env-file .env stop snowluma
fi
