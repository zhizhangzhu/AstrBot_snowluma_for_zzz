"""Check the local AstrBot OAuth provider without sending an inference request."""
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if line and not line.startswith("#") and "=" in line)
HOST = ENV.get("BIND_ADDRESS", "127.0.0.1")
if HOST == "0.0.0.0":
    HOST = "127.0.0.1"
BASE = "http://" + HOST + ":" + ENV.get("ASTRBOT_PORT", "6185")
MODEL_ID = "codex-oauth-gpt-6-luna"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
    with OPENER.open(req, timeout=5) as response:
        value = json.load(response)
    if value.get("status") == "error":
        raise RuntimeError("AstrBot local API reported an error")
    return value.get("data", value)


def main():
    credentials = json.loads((ROOT / "credentials.local.json").read_text())["AstrBot"]
    deadline = time.monotonic() + 55
    while True:
        try:
            auth = request("/api/auth/login", {"username": credentials["username"], "password": credentials["password"]})
            token = auth["token"]
            request("/api/v1/plugins/by-id?plugin_id=astrbot_plugin_codex_oauth_plug", token=token)
            request("/api/v1/providers/by-id?provider_id=" + MODEL_ID, token=token)
            break
        except (OSError, urllib.error.URLError, RuntimeError, KeyError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    config = request("/api/v1/config-profiles/default", token=token)["config"]
    if not config["provider_settings"]["enable"]:
        raise RuntimeError("AstrBot built-in AI is disabled")
    if config["agent_runner"]["config"]["model"]["provider_id"] != MODEL_ID:
        raise RuntimeError("AstrBot default model differs from the deployment configuration")
    oauth = request("/api/v1/plugins/config?plugin_id=astrbot_plugin_codex_oauth_plug", token=token)["config"]["oauth"]
    if oauth.get("oauth_access_token"):
        print("AstrBot 原生 AI 已启用，默认模型 gpt-6-luna，OAuth 登录已配置。")
    else:
        print("OAuth 插件已安装，等待账号授权后即可聊天。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("OAuth 提供商尚未就绪：" + type(exc).__name__ + "。请查看 AstrBot 日志。", file=sys.stderr)
        sys.exit(1)
