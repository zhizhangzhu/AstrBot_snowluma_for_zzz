# GitHub 下载访问修正

固定 AstrBot v4.29.1 的 repository.py，仅将源码 ZIP 下载入口改为官方 codeload.github.com。保留仓库解析、元数据校验、版本兼容检查与 TLS 证书校验。

通过 compose.yaml 只读挂载；升级 AstrBot 前应重新检查该文件与新版本的兼容性。原文件在 backups/github-download-before-* 中。
