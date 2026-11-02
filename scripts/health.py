import json
import subprocess
import sys
import urllib.request
from pathlib import Path

values = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines() if line and not line.startswith("#") and "=" in line)
host = values.get("BIND_ADDRESS", "127.0.0.1")
if host == "0.0.0.0":
    host = "127.0.0.1"
for label, key, default in (("AstrBot", "ASTRBOT_PORT", "6185"), ("SnowLuma", "SNOWLUMA_PORT", "5099"), ("QQ 登录桌面", "NOVNC_PORT", "6081")):
    try:
        with urllib.request.urlopen(f"http://{host}:{values.get(key, default)}/", timeout=5) as response:
            print(f"{label}: HTTP {response.status}")
    except Exception as exc:
        print(f"{label}: {type(exc).__name__}")

# Query inside the container network so the OneBot API is never published on the host.
probe = """const http=require('http');const fs=require('fs');const c=JSON.parse(fs.readFileSync('/app/data/config/onebot_2893214983.json','utf8'));const s=c.networks.httpServers.find(x=>x.enabled);const r=http.request({host:'127.0.0.1',port:s.port,path:'/get_login_info',method:'POST',headers:{Authorization:'Bearer '+s.accessToken,'Content-Type':'application/json'}},res=>{let b='';res.on('data',d=>b+=d);res.on('end',()=>{try{const v=JSON.parse(b);const d=v.data||{};console.log(JSON.stringify({status:v.status,retcode:v.retcode,user_id:d.user_id,nickname:d.nickname}));}catch(e){process.exitCode=1;}});});r.setTimeout(5000,()=>r.destroy());r.on('error',()=>{console.log('QQ 尚未登录，或 OneBot 尚未就绪');process.exitCode=1;});r.end('{}');"""
result = subprocess.run(["docker", "exec", "xiaoxiaoqing-snowluma", "node", "-e", probe], capture_output=True, text=True, timeout=12)
if result.returncode == 0:
    print("QQ 登录信息: " + result.stdout.strip())
else:
    print("QQ 登录信息: 尚未就绪；可打开 QQ 登录桌面检查。")

# Report configured native AI without sending an inference request.
try:
    from ensure_oauth import request
    credentials = json.loads(Path("credentials.local.json").read_text())["AstrBot"]
    token = request("/api/auth/login", {"username": credentials["username"], "password": credentials["password"]})["token"]
    config = request("/api/v1/config-profiles/default", token=token)["config"]
    enabled = config.get("provider_settings", {}).get("enable", False)
    model_id = config.get("agent_runner", {}).get("config", {}).get("model", {}).get("provider_id", "")
    oauth = request("/api/v1/plugins/config?plugin_id=astrbot_plugin_codex_oauth_plug", token=token)["config"].get("oauth", {})
    print(f"AstrBot 原生 AI: {'已启用' if enabled else '未启用'} / {model_id}")
    print("Codex OAuth 登录: " + ("已配置" if oauth.get("oauth_access_token") else "等待授权"))
    if "--check-model" in sys.argv:
        body = json.dumps({"provider_id": model_id}).encode()
        url = f"http://{host}:{values.get('ASTRBOT_PORT','6185')}/api/v1/providers/test"
        probe_request = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json", "Authorization":"Bearer "+token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(probe_request, timeout=70) as response:
            payload = json.load(response)
        result = payload.get("data", payload)
        available = result.get("status") == "available"
        print("AstrBot 内部模型实测: " + ("通过（已调用框架内模型实例）" if available else "失败（检查 provider 生命周期与日志）"))
        if not available:raise SystemExit(1)
    else:
        print("模型运行状态: 未实测；可用 --check-model 检查框架内的真实模型调用。")
except (OSError, ValueError, KeyError, RuntimeError):
    print("Codex OAuth 配置或模型检测暂不可读取。")
    if "--check-model" in sys.argv:
        raise SystemExit(1)
