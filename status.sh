#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/scripts/common.sh"
compose ps -a
python3 scripts/health.py
printf '\n旧服务状态：\n'
for name in "${legacy_services[@]}"; do
    docker inspect -f '{{.Name}}: {{.State.Status}} / restart={{.HostConfig.RestartPolicy.Name}}' "$name" 2>/dev/null || true
done
