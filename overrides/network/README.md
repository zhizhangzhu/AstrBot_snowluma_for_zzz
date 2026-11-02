# AstrBot 容器的默认代理策略

## 原因

AstrBot 4.29.1 在 http_proxy 留空时会清除进程内的小写代理环境变量。
此外，aiohttp.ClientSession 默认 trust_env=False，即使容器声明了代理，也不会自动读取。
因此，仅在 Docker env_file 中填写 HTTP_PROXY/HTTPS_PROXY 不足以覆盖插件。

## 当前部署

- AstrBot 原生系统配置的 http_proxy 使用既有公司代理；可在管理页面「设置 → 网络」中修改。
- no_proxy 保留已有条目，并补充 localhost、127.0.0.1、::1、AstrBot/SnowLuma 服务名和本机内网 IP。
- compose.yaml 仅给 AstrBot 添加只读 network 目录挂载、PYTHONPATH 和 XIAOXIAOQING_AIOHTTP_TRUST_ENV=1。
- Python 启动时加载 sitecustomize.py，为未传 trust_env 的 aiohttp 会话补上 True。
- requests、urllib、默认配置的 httpx 继续使用各自原生的环境代理支持。
- 显式 trust_env=False、显式代理、独立外部程序和原始 TCP/UDP 不受此默认值强制覆盖。
- 此处不修改 TLS 证书校验、插件源码、模型配置或 SnowLuma。

这是本部署的运行层适配，不是 AstrBot 官方的全流量转发功能。
已有 NO_PROXY 中的通配符/CIDR 并非每个 HTTP 库都支持；关键内部服务已使用精确主机名/IP 排除。
升级 Python/aiohttp/镜像时，应重新检查 sitecustomize 加载位置及会话初始化兼容性。

## 关闭与恢复

只关闭 aiohttp 默认策略：将 compose 中 XIAOXIAOQING_AIOHTTP_TRUST_ENV 改为 "0" 后，仅重建 astrbot 服务。
完全恢复：使用 .runtime/global-proxy-backup-path.txt 指向的备份恢复 compose.yaml，
再通过 AstrBot 配置 API 保存备份 default-config.json 中的 http_proxy 和 no_proxy 字段，然后仅重建 astrbot。
备份含敏感配置，保持目录权限 700、配置文件权限 600，不提交到版本库。

参考：https://docs.aiohttp.org/en/stable/client_advanced.html#proxy-support
