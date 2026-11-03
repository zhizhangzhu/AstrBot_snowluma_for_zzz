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
- 本地提交与远程发布分开；只有用户明确要求时才推送。用户已授权按桌宠计划书开发；生产切换、推送和 Releases 按计划书另行安排。

- 桌宠接入的需求与验收清单见 docs/desktop-pet-integration-plan.md，实现状态、限制及部署步骤见 docs/desktop-pet-implementation.md。
- QQ 与桌宠共享会话和记忆身份，实际来源与回复目标分别保留。修改追加消息、主动路由或共同身份时，覆盖跨端同时输入、生成中切端、设备撤销及两个人格隔离；不得将桌宠事件伪装成 QQ 戳一戳。
- 桌宠工具参数不得与实际框架调度参数（如 tool）冲突；须通过真实 FunctionToolExecutor 回归。人格解析兼容实际返回项数，普通输入和查询共用鉴权，不能只用简化替身证明入口可用。
- 表情等回复媒体须沿当前事件发送；共享 QQ 会话编号不代表本次回复目标是 QQ。桌宠发送失败不得降级为 QQ。换装只更新身体观察，不逐次触发模型；桌宠形象是同一人格的自身化身。故障与验收证据见 docs/desktop-pet-acceptance.md。
