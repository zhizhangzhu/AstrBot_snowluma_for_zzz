# 版本管理

父仓库： https://github.com/zhizhangzhu/AstrBot_snowluma_for_zzz

## 跟踪范围

- 部署脚本、Compose 文件、环境变量示例、补丁与操作文档。
- `plugins/` 中明确维护的插件源码。
- `Coopanion/` 子模块的提交号；客户端源码和提交历史留在其独立仓库。

`data/`、各实例的运行数据、账号配置、OAuth 登录态、密码、聊天与记忆、人格私有配置、表情图库、备份、故障现场和生成的安装包均不进入父仓库。
环境变量示例只包含空值或非敏感示例；实际 `.env` 留在本机。

当前已安装的陪伴、记忆、表情等插件在 `data/astrbot/plugins/`，不属于本次源码基线。现有 `runtime/` 也包含运行补丁与本地证据，继续排除。因此首次提交不是完整部署备份，也不能单靠检出仓库恢复当前 Bot。后续维护需要把指定源码或可复现补丁纳入受控目录，并在核验后部署；不要为此整体取消 `data/` 或 `runtime/` 的忽略规则。

## 桌宠子模块

```bash
git clone --recurse-submodules https://github.com/zhizhangzhu/AstrBot_snowluma_for_zzz.git
git submodule update --init --recursive
```

父仓库记录特定提交，不自动跟随客户端的 `main`。更新客户端后，先在客户端仓库提交并完成所需发布，再在父仓库提交新的子模块指针。父仓库不能代替客户端仓库保存其未提交文件或尚未推送的提交。

桌宠第一阶段面向 Windows 10/11 x64，安装包通过客户端仓库的 Releases 发布，不把二进制文件提交进源码。正式构建前需调整客户端更新源、应用标识和数据目录，并验证升级保留配对和设置。

## 提交与发布

按明确路径暂存，检查 `git diff --cached` 和 `git status` 后提交。不要使用 `git add -A`、`git add .` 或强制加入忽略的运行文件。

本地提交和推送分别进行。用户明确要求推送时，再推送已经检查的提交。功能开发、服务部署及客户端发布同样分别执行。
