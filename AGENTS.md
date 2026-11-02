# 小小情 QQ 机器人部署项目

- 本项目在同一个 AstrBot 面板（6185）管理小小情与初小晴。小小情 Bot QQ 为 2893214983，主要用户 1005145948；初小晴 Bot QQ 为 2510093206，主要用户 1207862759。
- 两个 QQ/SnowLuma 实例和设备目录独立；AstrBot 配置路由、陪伴人格数据和表情收集库分别隔离。不要把不同人格的主要用户或空间凭据混用。
- bots/chuxiaoqing 的旧独立 AstrBot 已停止，restart=no，standalone-legacy profile 不随日常启动启用。模型 OAuth 由统一面板中的一个插件独占刷新。
- 服务通过 compose.yaml 和 start.sh / stop.sh 管理。
- 旧的 cc-connect、NapCat 容器已暂停；启动新栈前确认旧栈没有运行。
- 保留 data/、.env、credentials.local.json 和旧项目数据；不要提交密码、令牌或 QQ 登录数据。
- 不要改动其他 workspace 项目，也不要自动向 QQ 好友/群发送测试消息。
- 数据迁移和服务操作应可恢复；优先使用官方镜像，固定版本和镜像摘要。

- 原生 AI 使用 astrbot_plugin_codex_oauth_plug（OAuth），默认模型 gpt-6-luna。
- HAPI Connector 已卸载，Hub/Runner 已暂停且 restart=no；legacy-hapi profile 不应随日常启动启用。
- OAuth 由插件独占刷新；不要重新启动旧 HAPI/cc-connect 来共享刷新同一份登录凭据。

- 两个 Bot 的 QQ 空间均使用各自 SnowLuma 原生接口（session_mode=onebot），后台运行不依赖浏览器；runtime/snowluma-native 保存固定版本补丁与回滚说明。

- 陪伴插件对 bot_accounts.json 中 auto_bind=true 的已登记实例自动核验连接和绑定（每 10 秒及首次聊天），不再要求扫码后运行绑定脚本。不同 QQ 不自动覆盖既有绑定，避免继承旧账号记忆。

- 修改发送、引用、消息入口或执行热重载时，须覆盖“回复生成期间重载，随后同会话继续聊天”：跨重载的事件账本不能仅依赖新类的 isinstance；发送包装必须幂等，引用锁不可被同一任务重复获取。运行 tests/test_send_tracking_hot_reload.py，并核验两个 Bot 的普通回复通路；仅检查插件加载成功、戳一戳可回复不足以证明正常。故障记录见 runtime/private-reply-stall-20261029/README.md。

## 版本管理

- 父仓库 origin 为 https://github.com/zhizhangzhu/AstrBot_snowluma_for_zzz.git，默认分支 main；Coopanion/ 是独立客户端子模块，父仓库固定其提交号。
- 跟踪范围与源码迁移约定见 VERSIONING.md；data/ 与 runtime/ 继续排除，不能把当前运行目录误认为已由 Git 完整备份。
- 按明确路径暂存并检查，不使用 git add -A、git add . 或 git commit -a；不强制加入凭据、用户记录、数据库或私有媒体。
- 本地提交与远程发布分开；只有用户明确要求时才推送。桌宠目前只准备源码与版本管理，功能开发须等待用户明确开始。

- 桌宠接入的已确认需求、默认行为、阶段和验收清单见 docs/desktop-pet-integration-plan.md；该计划书不代表用户已授权开始功能开发。
