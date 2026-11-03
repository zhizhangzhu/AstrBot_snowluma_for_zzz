#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/scripts/common.sh"
mkdir -p .runtime
exec 9>.runtime/lifecycle.lock
flock 9
"$PROJECT_DIR/bots/chuxiaoqing/stop.sh"
compose stop --timeout 30
if [[ -f .runtime/evolution.enabled ]]; then
    python3 scripts/evolution_service.py stop
fi
printf '小小情的 AstrBot 和 SnowLuma 已暂停，数据已保留。\n'
