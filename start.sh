#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/scripts/common.sh"
mkdir -p .runtime
exec 9>.runtime/lifecycle.lock
flock 9
# Keep both QQ stacks from signing in and replying concurrently.
for name in "${legacy_services[@]}"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
        docker update --restart=no "$name" >/dev/null
        if [[ "$(docker inspect -f '{{.State.Running}}' "$name")" == true ]]; then
            docker stop --time 20 "$name" >/dev/null
        fi
    fi
done
compose up -d
python3 scripts/ensure_oauth.py
"$PROJECT_DIR/bots/chuxiaoqing/start.sh" --qq-only
printf '\n小小情 QQ（2893214983）服务已启动。\n'
python3 scripts/urls.py
printf '查看状态：%s/status.sh\n查看管理密码：%s/credentials.sh\n' "$PROJECT_DIR" "$PROJECT_DIR"
if [[ "${1:-}" == --open ]]; then
    if command -v xdg-open >/dev/null 2>&1 && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
        xdg-open http://127.0.0.1:6185 >/dev/null 2>&1 || printf '请在浏览器打开上方 AstrBot 地址。\n'
    fi
fi
