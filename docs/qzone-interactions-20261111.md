# QQ 空间评论、点赞与记忆修复（2026-11-11）

小小情、初小晴继续使用各自 SnowLuma 原生账号。此次修复不改变账号绑定、主要用户、人格配置或 OAuth 刷新归属。

## 行为

- 原生详情读取保留说说 tid；评论去重使用说说与评论组合标识，内联回复另带父评论标识。原来的无说说编号 `post:N` 标记按文本指纹迁移，已确认回复与投递不确定记录保留；最近一天确实被编号冲突挡住的评论重新进入待处理队列，不重放旧历史。
- 按 `qzone_comment_inbox_recent_posts` 读取每条说说，一级评论最多续读十页。单条详情网络错误与其余说说隔离，已读数据继续处理，全失败则报告读取失败。服务端内联返回的楼中楼也保留，但此版本不能保证取得未内联返回的全部楼中楼。
- 回复使用原生 `reply_qzone_comment`，指向真实父评论；楼中楼可带被回复人的原生提及。使用上游确认的 `emotion_cgi_addreply_ugc`，不把普通追加评论当成楼层回复。写入前持久化投递不确定标记，中断或超时不自动重发。
- 每轮额度未处理完的评论保留待处理状态。升级后允许一次修复扫描，随后遵守已有轮询间隔；此修复部署时两个 Bot 均为 60 分钟。后续小小情已切换为 [5 分钟通知检查](qzone-notifications-20261111.md)，不保证每条评论都回复。
- 原生 `get_qzone_likes` 返回点赞者 QQ、昵称、总数和分页游标，不把地址、星座等无关字段传入插件。读取失败不等于无人点赞，部分页不证明某人未点赞。点赞接口不提供准确发生时间，记忆表述为“曾点赞”，不编造时间和动机。
- 已登记用户的评论、已发回复与确认的点赞通过真实记忆桥接保存。命名空间包含 Bot、人格、实际互动用户及该 Bot 的 QQ 会话；桌宠来源不会改变空间互动的身份。记录使用稳定 ID，记忆不可用时不影响发送结果，后续观察可重试保存。
- 主人格的默认空作用域解析为其已登记账号，避免后台空间维护只在副人格运行。

## 源码与运行文件

受控源码位于 `vendor/installed-plugins/astrbot_plugin_private_companion/`：`qzone_native_actions.py`、`qzone_comments.py`、`qzone_interactions.py`、`qzone_runtime.py`。更新所选文件在 `overrides/evolution-source-baseline.json` 的摘要，原始与修复后摘要保留在 `overrides/qzone-interactions/manifest.json` 及私有部署备份。

原生动作补丁位于 `overrides/qzone-interactions/native-*.js`，`scripts/prepare_qzone_interactions.py` 仅接受已经核验的固定 SnowLuma 输入摘要。它在现有 2.1.1 后台凭据和网络补丁之上增加动作，不升级整套 QQ 或 SnowLuma。

现有容器创建时未挂载 Compose 后来声明的 `runtime/index.mjs`，仅修改宿主文件并重启协议进程不会生效。发布工具因此核验宿主和两个容器内的实际 `/app/runtime/index.mjs` 摘要。需要更新无挂载的容器内副本时，只暂停协议进程；QQ 登录进程保持运行。后续按 Compose 重建时使用宿主同一文件。

## 检查与回滚

- 新增 Python 回归 12 项；最终连同跨重载普通回复 7 项一起运行，19 passed。
- 原有相关空间、账号会话与发送回归 49 passed；原生动作 3 项 Node 测试通过。测试使用真实 AstrBot 镜像，隔离网络和用户数据，没有发送 QQ 测试消息。
- 额外选择的旧用例有 8 项原先即失败：空间查看身份用例 7 项、事件处理器装饰断言 1 项。已用未修改的线上源码复现同样失败，私有日志为 `.runtime/qzone-interactions-20261111/baseline-tests.log`。这部分不能作为通过的验收证据。
- `python3 scripts/deploy_qzone_interactions.py check` 核对运行源码和两份容器内原生文件；`scripts/evolution_sources.py verify` 与 `verify-live` 核对受控基线。
- `python3 scripts/verify_qzone_interactions.py` 只读核验两个真实账号、最新说说、评论及点赞者；不发布或回复。

私有备份位于 `.runtime/qzone-interactions-20261111/deployment/`，包括所改源码、原始摘要和升级前互动状态快照，不进入 Git。部署工具的 `rollback` 要求先停止统一 AstrBot 和两个 SnowLuma 协议进程，恢复源码后再启动协议进程及 AstrBot。回滚源码不会覆盖升级后新增的聊天和记忆；状态快照仅供人工审计，不应整体覆盖正在使用的数据。

本次未推送或发布 Releases。

## 线上结果

2026-11-11 16:01 后，原生详情读回确认小小情在主要用户此前漏掉的评论下产生一条真实楼中楼回复；不是另发一条顶层评论。真实记忆库已经在小小情自身 Bot 及该用户的 QQ 会话命名空间下保存评论、点赞和回复记录，收件箱状态为 `checked:new=1,replied=1,skipped=0`，点赞读取状态为 `ok`。两个人格账号在线，新增原生接口读取和源码摘要检查通过。
