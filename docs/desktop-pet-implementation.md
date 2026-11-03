# 桌宠接入实现与验收

源码入口：服务端 `plugins/astrbot_plugin_desktop_pet/`，客户端 `Coopanion/core/astrbot/native.ts`、`native-bridge.ts` 与原生 `Coopanion/app/main.cjs`。需求基线为 [计划书](desktop-pet-integration-plan.md)。本文件区分源码检查、隔离测试和正式环境验收。

## 当前交付

- 新增独立桌宠插件和 Windows 客户端入口，保留原 Coopanion 入口。客户端仅运行形象、World 扩展和连接；人格、模型和主动决策仍由 AstrBot 负责。
- 使用 WSS 协议 v1，建议地址 `wss://10.74.4.16:6190/terminal/v1`。监听由可选 Compose 配置启用。2026-11-02 已部署插件 0.2.2；使用专用 CA 验证 TLS 通过，未认证连接返回 401。
- 私有 CA 签发包含 IP 的服务器证书；客户端仅为本连接信任该 CA。设备令牌服务端仅存哈希；Windows 端通过 Electron safeStorage 保存。配对文件通过可信渠道传输，不包含服务器私钥、QQ 登录态或模型凭据。
- 桌宠输入进入 AstrBot 事件队列，使用已核验 QQ 用户的会话和配置，实际平台仍为 `desktop_pet`。被动回复保持输入端；生成期间另一个终端的追加消息不会被并入错误的回复目标。
- 桌宠与 QQ 共用用户档案和私聊记忆域。记忆检索使用已验证共同身份，来源单独保存为 `source_platform=desktop_pet`。未把所有桌宠与所有 QQ 平台合并；另一个 Bot、人格或用户仍有隔离检查。
- 已连接、身体可见且可交互时，主动私聊优先桌宠，并抑制同一用户的主动 QQ 戳一戳。自服务端 0.2.4 起，隐藏、锁屏、休眠、合盖、熄屏或断网时，新主动交流改走 QQ；只有手动免打扰继续等待。应用心跳 45 秒过期。已经提交而结果未知的桌宠消息不在 QQ 重发。
- 桌宠提问由当前人格模型决定。普通对话、工具实际输出和合作插件发送的问话保留共同记录；后台表情人物询问可使用桌宠，继续沿用现有人格生成机制。
- World 能力按实际加载、启用和可用状态上报。提供分页能力查询、远程工具调用、状态和事件回传、真实图片结果、扩展市场安装/卸载及声明式配置。无需为每个普通工具再编写一层专用调用代码。
- 日程扩展接入现有预算和执行账本，保留每天一次的全天计划。桌宠目标可等待恢复，执行时再次核验能力和免打扰；连接心跳、状态刷新和动画不调用模型，也不触发全日重规划。
- 请求 ID 和收发账本持久化，重复事件不重复处理。展示、执行失败、取消、提交和未知分别记录；重连核对结果，不重做未知动作。
- 媒体传输携带实际字节和摘要，单批最多 8 个附件、合计 1 MiB。GIF 保留一份原始文件，不拆成多条独立消息。
- Windows 产品名 `AstrBot Desktop Pet`，独立应用标识及 AppData 目录。新增 x64 NSIS 构建流程，发布源指向自己的 fork，不向上游发布。

## 支持范围

客户端 0.3.1 使用原版完整原生窗口和 `console/` 界面，默认 DeepSeek 大肥鱼。装扮、形象包、扩展市场、配置、主题、聊天、语音和电脑操作页面复用原组件。主页与模型设置显示 AstrBot 连接；人格、共同记忆和模型决策由服务端提供。没有制作小小情专属美术。

标准 World 工具、即时事件、环境说明、声明式配置、前端包和生命周期通用接入；复杂配置保留原面板。密钥按设备和 World 使用 Windows CurrentUser DPAPI 保存，设置接口不返回明文。World 内部认知请求经过 AstrBot 主动协调与预算；依赖私有 Core API 或本地 Cortico Persona 的扩展仍需要适配，不能保证市场所有扩展无需修改即可运行。输出通知在生成后发送，不冒充逐字流式输出。

原生聊天页面保留本地事件，新增分页共同历史页面读取已绑定服务端私聊中的 user/assistant 正文，不展示推理或工具内部内容，不补造缺失的来源和时间。真实 Token 用量同步到原版用量页面。暂停或离线输入保留待发送状态；服务端已接收输入有持久化恢复，已经开始生成的输入不会因重连自动再次生成。

桌宠气泡文字有渲染确认；部分原生动作仅有提交回执，按 `submitted` 记录，未知执行不自动重放。GIF 以原始文件传递；超出附件上限明确拒绝，不能静默转为静态图。隐藏后恢复使用原托盘关闭并重新打开窗口的路径，再次唤起同时核验窗口可见性，不能仅依据连接仍在就认为桌宠可见。

升级使用本 fork 的 Windows 安装包，数据保留在 `%APPDATA%\AstrBotCoopanion`。首版未启用自动更新检查；构建配置仅确定本 fork 的发布来源。服务证书续期需要重载 AstrBot；更换 CA 需重新核验并导入配对文件。

## 源码与正式环境的边界

运行中的陪伴、记忆、表情插件位于忽略的 `data/`。本次以带 SHA-256 基线核对的补丁纳入 Git，未整体纳入聊天数据或现有插件目录：

| 文件 | 作用 |
| --- | --- |
| `overrides/desktop-pet/companion.patch` | 用户身份、被动/主动发送分流、平台能力及日程入口 |
| `overrides/desktop-pet/memory.patch` | 共同记忆域与真实来源分离，保留关系投影的来源核验 |
| `overrides/desktop-pet/meme-confirmation.patch` | 人物询问的主动终端选择及延后状态 |
| `overrides/desktop-pet/astrbot-follow-up.patch` | 不同来源终端各自保留回复目标 |
| `overrides/desktop-pet/terminal_integration.py` | 跨重载保留的主动发送上下文，仅作用于主动发送入口 |
| `scripts/desktop_pet_deploy.py` | 版本核对、准备、停机应用和回滚 |
| `compose.desktop-pet.yaml` | 挂载插件与框架补丁，开放独立 WSS 端口 |

`prepare` 已在部署前基线上验证可应用。2026-11-02 已完成正式部署与配对；QQ 会话和设备目录保留。重复部署前应核对已应用状态，不能把当前补丁后的目录误当成原始基线。

## 部署步骤

在项目目录执行。所有数据库和凭据继续留在忽略目录，不提交到 Git。以下命令须使用已安装依赖的 Python；可使用项目开发虚拟环境或 AstrBot 容器的 Python。

1. 记录当前版本，确认旧 HAPI、cc-connect、NapCat 服务没有运行。先完成 Windows 构建和隔离配对验收，再安排正式切换。
2. 运行 `python3 scripts/desktop_pet_deploy.py prepare`。脚本核对现有插件文件和 AstrBot 镜像中框架文件的哈希；不匹配会停止，不覆盖其他修改。
3. 使用安装了 `aiohttp`、`cryptography` 的 Python 生成专用证书：

   ```bash
   python3 -m plugins.astrbot_plugin_desktop_pet.manage \
     --root data/astrbot/plugin_data/astrbot_plugin_desktop_pet \
     certificates --ip 10.74.4.16
   ```

4. 仅停止 AstrBot，保留两个 SnowLuma/QQ 登录进程：

   ```bash
   source scripts/common.sh
   compose stop astrbot
   python3 scripts/desktop_pet_deploy.py apply --enable-schedule
   compose up -d --no-deps astrbot
   ```

   `apply` 拒绝在线覆盖；备份受影响文件后原子替换，保留文件属主和权限。新增 `.runtime/desktop-pet.enabled` 后，`scripts/common.sh` 自动加载可选 Compose 配置。`--enable-schedule` 仅在小小情主配置的已有扩展允许列表中增加本插件活动，不复制或修改初小晴档案。

5. 核验日志中三个兼容检查均通过：陪伴接口、记忆来源字段、框架跨端回复保护。WSS 没有有效设备认证时应返回 401；用正确 CA 验证 TLS，不能忽略证书错误。
6. 为小小情主要用户生成配对文件：

   ```bash
   python3 -m plugins.astrbot_plugin_desktop_pet.manage \
     --root data/astrbot/plugin_data/astrbot_plugin_desktop_pet \
     pair --persona-id 小小情 --bot-id 2893214983 \
     --user-id 1005145948 --qq-platform-id xiaoxiaoqing \
     --url wss://10.74.4.16:6190/terminal/v1 \
     --output /tmp/xiaoxiaoqing-desktop.local.json
   ```

   通过已有可信 SSH/SFTP 通道将文件传到自己的 Windows，核对服务器地址后导入。客户端保存加密副本；导入后删除传输副本。同一人格、Bot、用户的新配对会撤销旧设备；客户端不能自行更改 QQ 身份。
7. 用户自己从 QQ 和桌宠分别发起正常对话，核验双向话题衔接、称呼、主动优先级和真实摸头记录；同时检查初小晴普通对话。未经授权不向好友发送测试消息。

若用管理员身份创建配对文件，应仅给实际传输文件的用户读取权限；不要扩大到所有用户。服务端私钥仅由 AstrBot 服务读取。

### 撤销、续期和回滚

- 撤销设备：`manage --root <数据目录> revoke <device_id>`。后续帧和动作执行均会核验撤销状态。
- 使用同一 CA 续签服务器证书：`manage --root <数据目录> certificates --ip 10.74.4.16 --renew`。旧证书与私钥备份在同一私有目录中。先停止 AstrBot，再续签并启动；客户端信任根不变，无需重新配对。
- IP 改变时续签包含新 IP 的证书，核验新地址后重新导入配对文件；更换 CA 必须重新核验信任。
- 回滚：仅停止 AstrBot，执行 `python3 scripts/desktop_pet_deploy.py rollback backups/desktop-pet-<时间>`，再通过 `compose up -d --no-deps astrbot` 启动。代码和配置恢复，记忆、QQ 登录态和桌宠设备数据库不回滚。停用后旧配对不再提供远端能力。
- 后续升级现有插件前先核查基线哈希，更新补丁及测试；不要对不匹配版本强行应用。

## 开发检查与验收记录

最终逐项状态和限制以 [A01–A26 及后续 UI 验收清单](desktop-pet-acceptance.md) 为准。服务端 0.2.2 与客户端 0.3.1 已正式配对小小情；两个 QQ 登录实例保留且在线。客户端已在用户 Windows 安装并完成实际模型消息与工具展示回执检查。未自动向 QQ 好友发送测试消息。

最终服务端测试 46 项通过、1 项在本地跳过；实际 AstrBot 镜像中的消息、热重载、双人格和工具调度 30 项通过。客户端 511 项、World 200 项、三项类型检查通过；World 原有 3 项跳过。长期日程、自然日记、QQ 跨端接话和实际所需复杂扩展仍需人工体验。

部署备份 `backups/desktop-pet-20261102-122840`，表情投递补充备份 `backups/desktop-meme-route-20261102`。正式配对使用加密保存；维护连接和隔离测试服务不属于产品依赖。没有发布正式 Release 或合并 main。

## Windows 构建

客户端工作流 `.github/workflows/astrbot-terminal-windows.yml` 在 Windows x64 环境运行五项必需检查并生成 NSIS 安装包。工作流只上传构建产物，不发布 Releases。仓库规则要求先经授权推送特性分支并创建 PR，不能直接推送 main。

本地 Windows 构建：

```powershell
pnpm install --frozen-lockfile
pnpm run build:cortico
$env:COOPANION_TERMINAL = "1"
$env:PACK_ARCH = "x64"
pnpm exec tsx scripts/pack.ts
```

输出 `dist/AstrBot-Desktop-Pet-Setup-<version>.exe`。干净安装、配对、断线重连、锁屏与隐藏、升级保留配对、卸载不误删其他应用数据均需验收后才能正式发布。首版未配置代码签名，不声称已通过 Windows 发布认证。


## 原生界面验收与隐藏恢复

最终 Windows 0.3.1 构建和安装验收已通过：[运行记录](https://github.com/zhizhangzhu/Coopanion/actions/runs/41402802194)。客户端提交 `1c9982c896344a6cca9c1bbf097b8dc185eaf126`，用户电脑已安装并正式配对。原版原生 UI、DeepSeek 形象、共享身份只读、WSS 回复、隐藏恢复、暂停恢复与密钥加密均已实测。测试卸载曾清理单个残留执行文件，不能声称所有机器卸载均无残留。首版仍为手动安装升级，无代码签名和自动更新。

### 临时维护连接的确认与退出

`scripts/windows_maintenance.py` 与 `scripts/windows/Connect-AstrBotMaintenance.ps1` 使用单次 SSH 会话。Linux 侧在已认证 SSH 用户的权限内读取本地临时凭据，Windows 不再额外执行一次捕获凭据输出的 SSH 命令。PowerShell 在收到服务端握手后才显示 Connected，并为握手设置超时和 UTF-8 编码。

旧实现用线程阻塞读取 stdin；远端关闭后取消异步任务不能结束该线程，Python 最多等待 300 秒并输出 executor 警告。现改为可关闭的异步管道。`tests/desktop_pet/test_windows_maintenance.py` 使用真实子进程和本地 Unix socket 验证握手、命令往返与服务端关闭后及时退出。桌宠服务端测试目前共 36 项通过。

## AstrBot 管理的共享身份

客户端 0.3.1 通过绑定后的 `query:identity` 获取人格名、当前绑定主要用户的档案称呼、时区和模型标识。正常设置页、侧栏重命名与高级配置的共享字段只读；本地配置接收端也拒绝覆盖。每 30 秒读取一次服务端当前值，不调用模型；离线显示设备私有目录中上次验证的缓存。装扮、交互和本地界面语言仍可修改。各语言界面以 Bot 称呼角色，保留 Coopanion 产品名与既有形象实现。

正式只读接口已核对返回“小小情 / 猪猪 / Asia/Shanghai / codex-oauth-gpt-6-luna”，来源是小小情的人格和 1005145948 用户档案，没有写死客户端昵称。两个字段在原版页面中禁用并显示灰色，侧栏改名、高级设置和后端设置入口也不能覆盖 AstrBot 管理的共享身份。人类 UI 的 Coo 角色称谓统一为 Bot，品牌名保留。

## 2026-11-02 消息故障及防回归

1. AstrBot 4.29.1 的人格解析返回四项；旧测试替身只有三项。修复为只读需要的前两项，并使普通输入、查询与工具复用同一鉴权。此前替换遗漏导致普通输入引用未定义变量，客户端只显示排队消息清空。新测试直接执行实际入口，覆盖三项 / 四项、两个 Bot 和人格变化拒绝。
2. 工具公开参数 `tool` 与实际 FunctionToolExecutor 的调度参数同名，导致工具执行前报错。公开参数改为 `capability`，协议内部字段不变；测试经过真实框架调度，不再只直接调用插件函数。
3. 原 World 的独立运行说明可能让模型误以为普通回复不可见。接入提示明确由 AstrBot 负责回复目标；工具已经说出的内容不应重复。
4. 已确认的工具 say / ask 正文原来被历史查询的工具过滤一起隐藏；现只投影 completed 的可见文字，保留内部推理与工具细节过滤。去掉已知尾部用户 / 时间提示，不修改原始数据库。
5. 表情插件仅对部分平台使用事件发送；桌宠共用 QQ 会话身份后，兜底会发送到 QQ。已将桌宠加入事件路径，失败不降级 QQ，新增两个实际方法回归，并将修改纳入带哈希基线的补丁。
6. 换装原本逐次触发聊天，增加排队与上下文开销；现仅更新身体观察，下一次互动读取最新状态。戳动使用简短能力摘要，保留完整查询。明确形象是同一人格自己的身体，不能解释成另一个宠物；不删除真实对话来换取速度。

维护后再次检查普通消息、工具、图片的实际回复目标。加载成功、戳一戳单独可用、直接调用处理函数或只检查 UI 文本，都不足以证明完整链路正常。

## 0.3.2 展示与认知修复

服务端 0.2.3 增加桌宠互动批次、当前 QQ 头像同步，以及带稳定图片 ID 的发送认知。陪伴关系学习、话头合并、画像清理与可恢复源码覆盖层见[修复记录](dialogue-cognition-20261102.md)。

客户端 0.3.2 修复无效动作导致书名号内错误分段，支持头像更新、准确的服务器接收提示及身体气泡 PNG/GIF。书名号问题的回归位于 `Coopanion/packages/cortico-world-desktop-pet/tests/script.test.ts`，覆盖未知动作、成对引号与括号、嵌套标题、正常动作边界。Windows 构建记录为 [41423840198](https://github.com/zhizhangzhu/Coopanion/actions/runs/41423840198)；用户电脑是否完成安装以验收清单为准。

## 0.3.3 可见性和时间

服务端 0.2.4 对新主动发送做连通检查；不可见或不可达时选择 QQ，手动免打扰继续等待。客户端 0.3.3 报告 Windows 熄屏、合盖、锁屏及休眠，在消息上方显示原始日期和时间。未来日程的延期不能提前原安排，桌宠自主发言包含执行时钟，并应用当前人格的公共文字清理。诊断、备份与验证见[修复记录](desktop-availability-20261103.md)。
