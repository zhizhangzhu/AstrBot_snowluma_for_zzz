# 对话事实、画像和图片认知补丁

本层在已部署的桌宠集成（至少 0.2.2）之上应用。只维护清单列出的源码，不打包用户资料、数据库或图片。`manifest.json` 固定修改前后 SHA-256；`companion.patch`、`memory.patch`、`meme.patch` 是可核对的增量，新模块单独维护。

## 修改范围

- 已有成长整理任务学习当前人格与主要用户的关系事实，保存逐字证据；自动资料不覆盖手动关系文本，不更改权限、身份或关系评分模式。
- 保留较早的关系确认和完成反馈；未完话头按同义事项合并，旧片段不能重新打开已经结束的同一事项；结案仍要后续证据。不同日期、对象、任务不可混合。
- 职业抽取不再把“我当时……”等叙事当职业；用户明确自述的习惯与跨天统计推断分开。界面显示候选数量、覆盖日期和阈值。
- 图片以内容哈希作为稳定 ID，成功发送后记录原件身份、人物确认状态、情绪、文字、含义和认知版本。`meme_collection_status(query="image:<哈希前缀>")` 在当前人格可用图库中读取最新语义；元数据修正后查询立即反映变化，不逐帧重复识图或每次调用模型。
- 图片认知附在 Pydantic 私有槽位，不混入 QQ 的实际发送字段。未知人物和未验证语义明确留空，不把模型候选称为已确认。

桌宠事件合并位于父仓库 `plugins/astrbot_plugin_desktop_pet/interactions.py`，与本层一起部署，客户端不需要重装。动作和文字的真实发送目标继续由当前事件决定。

## 核验和部署

```bash
python3 scripts/dialogue_cognition_deploy.py check
```

重新应用时先确认匹配清单的基线，再仅停止 AstrBot，保持两个 SnowLuma 登录实例运行：

```bash
docker stop xiaoxiaoqing-astrbot
python3 scripts/dialogue_cognition_deploy.py apply
docker start xiaoxiaoqing-astrbot
```

`apply` 核验全部基线和准备结果后才写入，自动保留源码恢复点；完全已应用时只报告当前状态。修改后的源码、目标已出现未知文件或哈希不匹配均会停止，不能覆盖其他工作。

恢复仅回滚本层代码，不恢复用户记忆：

```bash
docker stop xiaoxiaoqing-astrbot
python3 scripts/dialogue_cognition_deploy.py rollback backups/dialogue-cognition-apply-<时间>
docker start xiaoxiaoqing-astrbot
```

2026-11-02 开发前源码备份为忽略目录 `backups/dialogue-cognition-20261102/`，其中包含定向画像修复前数据库和整理前人格资料。它不是可提交的发布内容。首次在线修复采用先备份源码、完成隔离测试、重启加载的方式；重现和后续应用应使用上述入口。
