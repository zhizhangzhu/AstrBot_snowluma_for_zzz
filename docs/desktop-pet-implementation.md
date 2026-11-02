# 桌宠接入实现与验收

源码入口：服务端 `plugins/astrbot_plugin_desktop_pet/`，客户端 `Coopanion/core/astrbot/native.ts`、`native-bridge.ts` 与原生 `Coopanion/app/main.cjs`。需求基线为 [计划书](desktop-pet-integration-plan.md)。本文件区分源码检查、隔离测试和正式环境验收。

## 当前交付

- 新增独立桌宠插件和 Windows 客户端入口，保留原 Coopanion 入口。客户端仅运行形象、World 扩展和连接；人格、模型和主动决策仍由 AstrBot 负责。
- 使用 WSS 协议 v1，建议地址 `wss://10.74.4.16:6190/terminal/v1`。监听由可选 Compose 配置启用。2026-11-02 已部署插件 0.2.0；使用专用 CA 验证 TLS 通过，未认证连接返回 401。
- 私有 CA 签发包含 IP 的服务器证书；客户端仅为本连接信任该 CA。设备令牌服务端仅存哈希；Windows 端通过 Electron safeStorage 保存。配对文件通过可信渠道传输，不包含服务器私钥、QQ 登录态或模型凭据。
- 桌宠输入进入 AstrBot 事件队列，使用已核验 QQ 用户的会话和配置，实际平台仍为 `desktop_pet`。被动回复保持输入端；生成期间另一个终端的追加消息不会被并入错误的回复目标。
- 桌宠与 QQ 共用用户档案和私聊记忆域。记忆检索使用已验证共同身份，来源单独保存为 `source_platform=desktop_pet`。未把所有桌宠与所有 QQ 平台合并；另一个 Bot、人格或用户仍有隔离检查。
- 已连接、身体可用且可交互时，主动私聊优先桌宠，并抑制同一用户的主动 QQ 戳一戳。隐藏、锁屏、休眠和免打扰会等待，锁屏或休眠导致断连时也保留临时等待；正常退出会解除临时等待，免打扰继续保留；普通断连且未保持免打扰时允许之后的新主动交流使用 QQ。已经提交而结果未知的桌宠消息不立即在 QQ 重发。
- 桌宠提问由当前人格模型决定。普通对话、工具实际输出和合作插件发送的问话保留共同记录；后台表情人物询问可使用桌宠，继续沿用现有人格生成机制。
- World 能力按实际加载、启用和可用状态上报。提供分页能力查询、远程工具调用、状态和事件回传、真实图片结果、扩展市场安装/卸载及声明式配置。无需为每个普通工具再编写一层专用调用代码。
- 日程扩展接入现有预算和执行账本，保留每天一次的全天计划。桌宠目标可等待恢复，执行时再次核验能力和免打扰；连接心跳、状态刷新和动画不调用模型，也不触发全日重规划。
- 请求 ID 和收发账本持久化，重复事件不重复处理。展示、执行失败、取消、提交和未知分别记录；重连核对结果，不重做未知动作。
- 媒体传输携带实际字节和摘要，单批最多 8 个附件、合计 1 MiB。GIF 保留一份原始文件，不拆成多条独立消息。
- Windows 产品名 `AstrBot Desktop Pet`，独立应用标识及 AppData 目录。新增 x64 NSIS 构建流程，发布源指向自己的 fork，不向上游发布。

## 支持范围

客户端 0.3.0 使用原版完整原生窗口和 `console/` 界面，默认 DeepSeek 大肥鱼。装扮、形象包、扩展市场、配置、主题、聊天、语音和电脑操作页面复用原组件。主页与模型设置显示 AstrBot 连接；人格、共同记忆和模型决策由服务端提供。没有制作小小情专属美术。

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

`prepare` 已在当前安装基线上验证可应用。正式运行插件、模型、QQ 会话和设备目录没有在准备过程中被替换。生产部署和推送仍按计划书另行安排。

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

本轮隔离测试使用临时数据库、真实本机 WSS 端口和真实 World；模型决策不访问在线供应商，测试没有向 QQ 发送消息。开发工具、日志、准备结果和隔离副本在忽略目录 `runtime/desktop-pet-development/`、`.runtime/desktop-pet/` 和客户端 `scratch-*.log`。

| 检查 | 当前结果与覆盖范围 |
| --- | --- |
| 父仓库 `tests/desktop_pet` | 35 项通过；含 Python WSS ↔ TypeScript 客户端 ↔ 原生桌宠 World 的文字展示与确认 |
| 客户端 `pnpm run test` | 511 项通过，64 个测试文件 |
| 客户端 `pnpm run test:worlds` | 200 项通过，3 项按原测试条件跳过 |
| 客户端三项类型检查 | 三项均通过：`typecheck`、`typecheck:web`、`typecheck:worlds` |
| 陪伴与发送回归 | 95 项、9 个子测试通过（含两项框架跨端追加消息检查）；覆盖两个人格普通回复作用域、热重载和日程 |
| 框架与退役实例回归 | 跨端追加消息 2 项通过（已包含于上述 95 项）；退役实例回调另 2 项通过 |
| 记忆回归 | 最终合并运行 29 项通过；覆盖唤醒、共同身份、同用户跨端检索及另一个人格拒绝 |
| 表情人物询问回归 | 14 项通过；涵盖桌宠等待时不绕道 QQ、人格问话内容及不借用 QQ 引用 ID |
| Windows 原生窗口与安装升级 | 已在用户 Windows 的独立临时目录安装旧版包并验证原生窗口、加密配对与 WSS 展示回执；0.3.0 的 Windows CI 已通过实际 NSIS 安装、完整原生 UI、WSS 配对、聊天展示回执、隐藏恢复及重装保留配对；用户电脑的最终安装与正式配对仍待维护通道恢复 |
| 正式 WSS 和现有双 Bot | 已部署服务端 0.2.0；两个 QQ 实例分别确认 2893214983 / 2510093206 在线，模型配置及 OAuth 保留；客户端尚未绑定正式会话 |

A01–A24 的主要服务端行为已有源码及分层测试，不能据此勾选 Windows 实机全部通过。A04/A05/A09/A11/A20/A21/A23 仍需在最终安装包与正式配对下核验；A25 需完成安装、升级与卸载验收。用户已授权远程发布和切换；客户端 PR 为 https://github.com/zhizhangzhu/Coopanion/pull/1 ，版本 0.3.0 的源提交为 `bbc36287e758d603aa297a560c937ce02e1f6224`。协议保持 `1`，补丁基线见 `overrides/desktop-pet/manifest.json`。

2026-11-02 部署备份为 `backups/desktop-pet-20261102-122840`；13:39 重启 AstrBot 载入最终服务端，两个 SnowLuma 未重启。真实框架下追加执行 29 项消息、热重载和双人格回复范围回归，全部通过。未向 QQ 发送测试消息。Windows 临时维护连接已断开，重连问题正在等待用户处理；当前桌宠仍使用隔离验收身份，未连接小小情正式会话。

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

2026-11-02 的 Windows 构建与安装验收已通过：[运行记录](https://github.com/zhizhangzhu/Coopanion/actions/runs/41395180081)。安装包为 `AstrBot-Desktop-Pet-Setup-0.3.0.exe`，产物名 `AstrBot-Desktop-Pet-Windows-x64`，含验收报告及原生窗口截图。产物 ZIP 的 SHA-256 为 `918a75870fc53d95bc5e8a4139936b85dc3d83248500b4734fec4f9cc5cd6e99`。普通 CI 的 Linux、Windows 和 macOS 检查均通过。首次正式 Releases、用户电脑实机验证及正式配对尚未完成。

隐藏后的窗口仍可能维持连接。旧的再次唤起判断只检查连接，因而漏掉“连接正常但窗口隐藏”的状态。原生 `app/main.cjs` 的 `ensurePet` 同时检查 `connected` 与 `onDesktop`；托盘显示使用 `showPet` 的关闭/重新打开窗口路径。Windows CI 使用同一公开面板路径验证隐藏、关闭、重新打开后 `connected` 和 `onDesktop` 均恢复。实际托盘点击和多显示器位置还需在用户电脑核验。

当前生产配对文件已在服务器的私有 `.runtime/` 中生成，绑定小小情与用户 1005145948，未发送到未核验的设备。配对凭据、私有 CA、数据库、用户记录和截图均未提交。父仓库 PR 为 https://github.com/zhizhangzhu/AstrBot_snowluma_for_zzz/pull/1 。
