#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
exec /usr/bin/python3 - <<'PYCODE'
from pathlib import Path
import json
qq=json.loads(Path('credentials.local.json').read_text())
main=json.loads(Path('../../credentials.local.json').read_text())['AstrBot']
print('统一 AstrBot: http://10.74.4.16:6185')
print('用户名: '+main['username'])
print('密码: '+main['password'])
print('初小晴 SnowLuma: http://10.74.4.16:5100')
print('密码: '+qq['SnowLuma']['password'])
print('初小晴 QQ 桌面: http://10.74.4.16:6082/vnc.html?autoconnect=1&resize=scale')
print('桌面密码: '+qq['VNC']['password'])
PYCODE
