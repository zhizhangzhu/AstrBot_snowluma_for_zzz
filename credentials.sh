#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/scripts/common.sh"
python3 scripts/credentials.py
