from pathlib import Path

values = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines() if line and not line.startswith("#") and "=" in line)
host = values.get("BIND_ADDRESS", "127.0.0.1")
if host == "0.0.0.0":
    host = "127.0.0.1"
for label, key, default in (("AstrBot 管理面板", "ASTRBOT_PORT", "6185"), ("SnowLuma 管理面板", "SNOWLUMA_PORT", "5099"), ("QQ 登录桌面", "NOVNC_PORT", "6081")):
    print(f"{label}（本机）：http://{host}:{values.get(key, default)}")
lan = values.get("WEBUI_LAN_ADDRESS")
if lan:
    for label, key, default in (("AstrBot 管理面板", "ASTRBOT_PORT", "6185"), ("SnowLuma 管理面板", "SNOWLUMA_PORT", "5099")):
        print(f"{label}（内网）：http://{lan}:{values.get(key, default)}")
lan = values.get("NOVNC_LAN_ADDRESS")
if lan:
    port = values.get("NOVNC_PORT", "6081")
    print(f"QQ 登录桌面（内网）：http://{lan}:{port}/vnc.html?autoconnect=1&resize=scale")
