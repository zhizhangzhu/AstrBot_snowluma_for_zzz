#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
compose() { docker compose --project-directory "$PROJECT_DIR" --env-file "$PROJECT_DIR/.env" -f "$PROJECT_DIR/compose.yaml" "$@"; }
legacy_services=(cc-connect-qq cc-connect-qq-agent cc-connect-napcat-webui-lan napcat-cc-connect napcat-cc-connect-2 xiaoxiaoqing-codex-hub xiaoxiaoqing-codex-runner)
