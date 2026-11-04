#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/scripts/common.sh"
mkdir -p .runtime
exec 9>.runtime/lifecycle.lock
flock 9
# Finish or cancel publisher work before the final AstrBot stop; an in-flight
# activation must not restart AstrBot after the project has been stopped.
if [[ -f .runtime/evolution.enabled ]]; then
    python3 scripts/evolution_service.py stop
fi
"$PROJECT_DIR/bots/chuxiaoqing/stop.sh"
compose stop --timeout 30
printf '小小情的 AstrBot 和 SnowLuma 已暂停，数据已保留。\n'
