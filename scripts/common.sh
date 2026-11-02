#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
compose() {
  local files=(-f "$PROJECT_DIR/compose.yaml")
  if [[ -f "$PROJECT_DIR/.runtime/desktop-pet.enabled" ]]; then
    [[ -f "$PROJECT_DIR/.runtime/desktop-pet/follow_up.py" ]] || { echo 'Missing prepared desktop transport guard' >&2; return 1; }
    files+=(-f "$PROJECT_DIR/compose.desktop-pet.yaml")
  fi
  docker compose --project-directory "$PROJECT_DIR" --env-file "$PROJECT_DIR/.env" "${files[@]}" "$@"
}
legacy_services=(cc-connect-qq cc-connect-qq-agent cc-connect-napcat-webui-lan napcat-cc-connect napcat-cc-connect-2 xiaoxiaoqing-codex-hub xiaoxiaoqing-codex-runner)
