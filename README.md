桌宠首版源码、测试状态与部署回滚步骤见 [桌宠实现与验收](docs/desktop-pet-implementation.md)。当前未切换生产环境。

桌宠接入需求与后续实施核对见 [AstrBot × Coopanion 桌宠接入计划书](docs/desktop-pet-integration-plan.md)。

版本管理范围、桌宠子模块和 Windows 发布约定见 [VERSIONING.md](VERSIONING.md)。

## 同一面板管理两个 Bot（2026-10-27）

统一入口 http://10.74.4.16:6185 。人格、Astr 配置及陪伴面板均提供“小小情”和“初小晴”；初小晴主要用户为 1207862759，QQ 登录桌面使用 6082。两边使用独立 QQ 设备目录、陪伴数据、记忆归属及表情收集库。详情见 [初小晴说明](bots/chuxiaoqing/README.md)。原 6186 独立 AstrBot 已暂停，数据保留。根目录启动/停止入口管理统一栈及两个 QQ 实例。

# 小小情 QQ 机器人

QQ：**2893214983（小小情）**。当前链路：**QQ → SnowLuma → AstrBot 原生 AI → Codex OAuth 模型**。

## 快捷操作

```bash
cd /home/yingjxu/workspace/astrbot-snowluma
./start.sh        # 启动 AstrBot 和 SnowLuma，检查 OAuth 提供商配置
./stop.sh         # 暂停当前服务，保留数据
./status.sh       # 查看 QQ 登录、原生 AI 和服务状态
./credentials.sh  # 在本机查看管理密码
./logs.sh         # 查看日志，Ctrl+C 只退出查看
```

workspace 下的 `启动小小情.sh`、`停止小小情.sh`、`小小情状态.sh` 仍可使用。桌面快捷方式会启动服务并打开 AstrBot 管理面板。

## 内网入口

| 页面 | 地址 | 用户名 |
| --- | --- | --- |
| AstrBot 管理面板 | http://10.74.4.16:6185 | admin |
| SnowLuma 管理面板 | http://10.74.4.16:5099 | admin |
| QQ 登录桌面 | http://10.74.4.16:6081/vnc.html?autoconnect=1&resize=scale | 仅需桌面密码 |

本机也可通过 127.0.0.1 访问相同端口。当前管理密码与 QQ 桌面密码见 `credentials.local.json`，文件权限为 0600。OneBot API 仅在 Docker 网络内可达。

QQ 登录桌面是 SnowLuma Docker 自带的 noVNC 远程桌面，用于操作 Linux QQ；SnowLuma 自身的管理面板在 5099 端口。

## 原生 AI 与 OAuth

- 插件：`astrbot_plugin_codex_oauth_plug`，固定提交见 `backups/deployment-manifest.json`。
- 默认模型：`gpt-6-luna`，推理强度 `low`，无需 OpenAI API Key。
- AstrBot 原生 AI 已启用。可在「配置文件 → AI」选择模型，在「人格设定」管理人设。
- 普通私聊进入 AstrBot 的原生对话流程，无需 `/hapi` 或 `>` 前缀。
- 账号登录从已暂停的 Codex 服务迁移；OAuth 刷新由新插件独占。不要启动旧 HAPI 或旧 cc-connect 来刷新同一份凭据。
- 授权命令仅管理员 QQ `1005145948` 可用。需要重新授权时发送 `/codex_oauth_start`，按插件说明完成绑定。
- OAuth 凭据保存在 `data/astrbot/config/astrbot_plugin_codex_oauth_plug_config.json`；不要公开此文件或含凭据的截图。
- 模板中的 Key 占位符是插件内部标记，不是需要申请的 API Key。
- 旧 Codex Agent 的工作目录、Skills、人格、长期记忆与历史会话没有自动转换为 AstrBot 数据。
- 知识库的向量化模型需另行配置；聊天模型的 OAuth 不自动提供 Embedding 服务。

## HAPI 与旧服务

`astrbot_plugin_hapi_connector` 已先停用后卸载，插件配置已删除；迁移前文件保存在权限为 0600 的 `backups/before-codex-oauth-*.tar.gz`。

HAPI Hub、Runner 容器已暂停，重启策略为 `no`，其持久化数据保留。`compose.yaml` 中它们归入 `legacy-hapi` profile，日常快捷启动不会启用。旧的 `ensure_codex.py` 已从启动路径移除并归档。

`cc-connect-qq`、`cc-connect-qq-agent`、`cc-connect-napcat-webui-lan`、`napcat-cc-connect`、`napcat-cc-connect-2` 继续暂停，重启策略为 `no`。不要同时运行新旧 QQ 协议端。

## 网络与登录

QQ 和 OAuth 插件使用已获授权的公司代理 `http://proxy.esl.cisco.com:80`。QQ 原生代理保存在客户端持久化配置，OAuth 代理位于插件的「运行设置」。

QQ 已勾选自动登录；当前环境重建 SnowLuma 容器后曾要求再次扫码，不能保证每次启动都免扫码。本次 OAuth 迁移只重启 AstrBot，保留了 QQ 登录。

## 目录与记录

- `compose.yaml`：当前服务与已停用 HAPI profile。
- `.env`：镜像、内网端口、OneBot 访问令牌与桌面密码。
- `data/astrbot/`：原生 AI、人设、插件、OAuth 凭据与会话数据。
- `data/snowluma/`、`data/qq-config/`、`data/qq-share/`：SnowLuma 配置和 QQ 持久化数据。
- `backups/`：迁移前配置、固定版本与旧部署记录；不要公开。
- `deployment-state.json`：当前部署状态和待处理事项。

## 上游资料

- https://github.com/AstrBotDevs/AstrBot
- https://github.com/RhoninSeiei/astrbot_plugin_codex_oauth_plug
- https://snowluma.github.io/en/docs/guide/deploy/docker

## 插件市场源

默认市场主站在当前网络经公司代理访问超时。已在插件源管理中添加「官方 GitHub 备用源」，地址为：

https://raw.githubusercontent.com/AstrBotDevs/AstrBot_Plugins_Collection/main/plugin_cache_original.json

打开「插件 → 插件市场」，在插件源选择器中选择「官方 GitHub 备用源」。已通过 AstrBot 管理接口确认可以读取 1329 项插件。未被该市场收录的插件仍需通过仓库链接安装；当前 Codex OAuth 插件已安装，不必重复安装。

## 插件安装网络

插件市场列表与插件源码下载是两个请求。当前环境中 `github.com/.../archive/...zip` 访问失败，已通过只读挂载 `overrides/repository.py` 将源码 ZIP 链接调整为官方 `codeload.github.com`，元数据、版本与 TLS 校验保留。截图中的 Anysearch 仓库预检约 0.6 秒、源码下载约 2 秒完成，未自动安装该插件。

安装窗口选择「不使用 GitHub 加速」，不要选择证书错误的 `gh.dpik.top`。若旧窗口仍在转圈，取消并刷新页面后重开。AstrBot 的全局代理配置没有变更。升级 AstrBot 镜像前，请按 `overrides/README.md` 检查下载修正与新版本的兼容性。

## OAuth 会话参数兼容

AstrBot v4.29.1 的实际聊天会向提供商传递 `conversation_id`。OAuth 插件 v0.4.1 的 `_query` 未声明此参数，曾导致 QQ 聊天报 TypeError。已在普通和流式查询中显式接收这个参数；本地会话标识不发往 Codex Responses，历史仍由 AstrBot 的 `contexts` 传递。流式入口也同步提取并传递该参数。

兼容补丁保存在 `overrides/oauth-conversation-compat.patch`，原文件在 `backups/oauth-provider-before-conversation-fix-*`。更新 OAuth 插件后应检查上游是否已修复；不要直接覆盖新版本的方法实现。

## 陪伴与长期记忆插件

已安装并启用：

| 插件 | 固定版本 | 管理入口 |
| --- | --- | --- |
| 我会永远陪着你 | 6.7.0 | 插件详情 → 陪伴面板 |
| 我会牢牢记住你 | 2.3.1 | 插件详情 → 记忆面板 |

刷新管理页面后可打开插件视图，并使用 AstrBot 的侧栏置顶入口。陪伴插件同时提供 `companion-panel` 和“陪伴面板”两个别名入口，使用中文入口即可。

小小情原有 AstrBot 人格和原生 AI 配置保持原样；陪伴插件主人格绑定“小小情”，作用域限定 QQ 2893214983。插件后台模型和记忆总结均选择现有 `codex-oauth-gpt-6-luna`，不新增 API Key 或 Embedding 服务。

当前已启用记忆捕获、自动阶段总结、记忆注入及两个插件的记忆协同；表达学习规则需要审核。主动发送、虚构生活日程/身体状态、插件自己的群聊增强和 QQ 空间自动发布暂时关闭，可在陪伴面板按需设置；AstrBot 原有群聊配置继续生效。旧 cc-connect 的记忆没有自动导入。

两个插件使用上游原始安装包，没有修改插件源码。为满足 6.7.0 的 Pillow 版本范围，AstrBot 基于原官方镜像增加依赖层，Pillow 为 12.3.0；当前镜像 ID 由 `.env` 的 `ASTRBOT_RUNTIME_IMAGE` 固定，日常启动入口保持不变。版本、提交、安装包哈希、依赖和备份位置见 [安装记录](runtime/astrbot-companion/manifest.json)。源码安装包保存在 `runtime/plugin-packages/`，通过文件上传安装，因此不会自动更新到 7.0 测试版。

重建依赖镜像时，在项目目录使用 `.env` 中的原 `ASTRBOT_IMAGE` 作为 `BASE_IMAGE` 构建 `runtime/astrbot-companion/Dockerfile`，再将新镜像 ID 写入 `ASTRBOT_RUNTIME_IMAGE`，仅重建 AstrBot 服务。升级 AstrBot 或陪伴插件前应重新核对依赖范围。

安装前备份位于 `backups/before-companion-*`，包含原 AstrBot 数据、人格、原生配置和部署文件。安装检查确认两个插件已加载、管理页面与后端可访问、QQ 保持在线；没有自动向 QQ 好友或群发送测试消息，也没有额外发起模型推理测试。

## 插件联网的全局代理

现已启用 AstrBot 原生全局 HTTP 代理，并为容器内未指定 trust_env 的 aiohttp 请求启用环境代理。
天气等插件无需各自修改源码；管理页面「设置 → 网络」可查看全局代理。
本机和内部服务通过 no_proxy 保持直连，显式禁用代理的插件及外部程序不在默认适配范围。
运行层适配及关闭方法见 overrides/network/README.md；已有启动脚本会保留此配置。

## 表情包管理器

已安装 meme_manager 5.2.5，源码固定到 cf18e81c84fb3a713320addd57e56014d499f65c。
入口：AstrBot → 插件 → 表情包管理器 → 管理页面。
使用原生分类标签，由当前聊天模型判断表达意图；每条回复最多 1 张，低频数量提示已开启。
小小情当前绑定已下载的「橘雪莉表情包」（38 张），官方初始包（398 张、19 类）也已入库，可在管理页面切换。
图库切换：设置中心 → 使用规则 → 小小情人格规则，选择图包后保存规则。
自动收集和语义向量检索默认未启用；陪伴插件自带的表情表达实验保持关闭。
普通聊天使用 AstrBot 插件管线；陪伴插件自行发送的后台主动消息，尚未额外开发管理器联动。
依赖已加入固定运行镜像，启动脚本仍正常使用 .env 的 ASTRBOT_RUNTIME_IMAGE。
版本、依赖锁定、图包来源和恢复用备份路径见 runtime/astrbot-meme-manager/manifest.json。
未调用模型验证选图，未向 QQ 自动发送测试消息。

## 自动收集用空图库

已创建「收集来的表情包」（collected-memes），复制官方初始包全部 19 个分类及说明，创建时不包含图片。
自动收集目标已指定为该组，当前启用开关已由用户打开，人工审核开启；视觉模型尚未选择，识别任务暂不会启动。
分类来源、创建记录与恢复备份见 .runtime/collected-meme-pack.json。


### 表情包自动收集与陪伴插件的处理顺序

已为表情包管理器 5.2.5 的收图入口设置优先级 1000，使原图先进入自动收集，再交给陪伴插件处理；人工审核和筛选门槛保留。细节、补丁和回滚方式见 `overrides/meme-manager/README.md`。插件升级或重装后需复核此补丁。


### 已收集表情的按人物检索

已增加基于现有描述/标签的本地精确找图入口，复用表情管理器候选 ID 投递流程；普通分类选图保留。后续自动收集优先识别人名，并可使用用户确认的人物参考图。你手工编辑的语义不重写，当前人工审核关闭状态保留。配置说明、补丁与回滚见 `overrides/meme-manager/README.md`。插件升级后需复核本地兼容补丁。


### 未知人物的聊天确认

后续新收集的未知角色图片具有独立人物状态：待确认／已搁置／已确认，与分类审核分开。向管理员私聊只问一次；岔开话题或不认识就搁置，不自动重问、不自动入库。以后引用该图或“待定图#编号”明确告诉角色名，可补标签并按当前审核规则接收。状态、回滚与数据位置见 `overrides/meme-manager/README.md`。


## 主动功能修复（2026-10-25）

已开启陪伴插件的主动消息生成，修复原生戳一戳、AnySearch 继承和内部生成时的记忆工具协议；联网请求显式使用现有代理。已按本次授权向管理员测试一条主动消息和一次戳一戳。QQ 空间 Cookie 仍被服务端判为未登录，自动发布和评论监听维持当前关闭状态。此前安装说明中的默认配置为安装时状态，当前结果、补丁与回滚见 [主动功能修复](overrides/companion-proactive/README.md)。


## 功能自知已并入陪伴插件（2026-10-25）

功能自知现为「陪伴面板 → 配置 → 通用能力」内的主开关，带摘要、近期动作、联系人查询和受限聊天调参子开关；仅管理员私聊生效。聊天改配置入口已实现，需先在面板开启，默认关闭；支持白名单开关/数值、保存记录和撤销。独立插件已卸载，原配置及数据保留。

部署说明、配置路径、QQ空间登录方法与恢复方式见 [功能自知集成说明](runtime/embedded-function-awareness/README.md)。当前QQ好友可读7位、已建档对象1位；QQ客户端在线，QQ空间仍需有效浏览器Cookie。消息合并继续保持文本等待4秒、最长12秒、最多8条，智能文本收口关闭。


## QQ空间关注用户监听（2026-10-25）

已并入「QQ空间动态 → 关注用户监听」子配置并开启，只关注管理员1005145948，每5分钟检查；首次19条动态仅建立基线。模型可选择发起私聊，点赞和公开评论分别可选且默认关闭；私聊沿用已有主动候选审核。源码、限额、状态、验证范围与恢复见 [关注监听说明](runtime/qzone-watch/README.md)。


## 戳一戳确认与措辞（2026-10-25）

主动戳一戳现区分API请求提交与目标匹配的QQ事件确认，未确认不追加重试或宣称成功；反戳先确认动作再说完成。此次17:06:04确有QQ服务端戳一戳事件，接收端未显示原因仍待授权实发确认。证据、修改与回滚见 [戳一戳修复说明](runtime/poke-receipts/README.md)。


## QQ空间间歇连接失败（2026-10-25）

空间只读请求现支持原总超时内最多3次重连，继续使用现有代理与TLS校验；写操作不重发。故障复现为连接重置，同一Cookie随后读到小小情自己的动态和管理员最近10条动态。配置入口、诊断范围和恢复见 [只读网络重连](runtime/qzone-network-retry/README.md)。


## 空间账号归属（2026-10-25）

已修正启用多人格就将QQ空间判为共享账号的问题。现在根据真实会话QQ识别自己的账号与用户账号，同时把账号历史动态和当前程序实际发布记录分开。自己的账号、管理员空间均只读核验成功，自动发布保持关闭。证据与恢复见 [空间身份说明](runtime/qzone-account-identity/README.md)。


## 自主刷好友空间与重点监听并存（2026-10-25）

已开启好友动态浏览，每30分钟最多10条、判断2条新内容，择机点赞或向有许可的作者私聊；公开评论关闭，浏览每天总计3次、冷却60分钟。重点关注仍每5分钟，用户已开启的生活说说与关注点赞保留。首次自然后台抓取6条并建基线，未补互动旧内容。配置、数据迁移和恢复见 [好友浏览说明](runtime/qzone-friends-browse/README.md)。

## 2026-10-27 双 Bot 后台 QQ 空间与所在地核验

同一 AstrBot 面板管理小小情（2893214983）和初小晴（2510093206）。QQ 空间改用各自 SnowLuma 原生接口，后台可直接读取，不要求用户打开网页或保留窗口。初小晴所在地及天气为哈尔滨，主要用户 1207862759；小小情重点关注仍为 1005145948。陪伴功能开关保持一致，各自用户、记忆、配置与账号路由隔离。原生空间修复、线上只读验证与回滚说明见 [运行补丁](runtime/snowluma-native/README.md)，综合核验见 [报告](runtime/bot-qzone-audit/REPORT.json)。本次未发送 QQ 测试消息或制造空间互动。

2026-10-28：修复私聊自报称呼同步到本地及统一档案、学习页面跨人格旧缓存，以及真实日记遗漏已确认聊天。已按对应账号恢复称呼并修订相关日期日记；记录与回滚见 [对话事实修复](runtime/conversation-facts-repair/README.md)。
