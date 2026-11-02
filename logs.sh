#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/scripts/common.sh"
compose logs --tail 80 --follow "$@"
