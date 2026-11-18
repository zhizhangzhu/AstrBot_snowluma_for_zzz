# real_schedule/runtime.py：延期保留未来时段

本层在桌宠 0.2.4 与 dialogue-cognition 源码层之上应用。`manifest.json` 固定修改前后哈希；只改变动态日程的延期计算和对应回归测试，不包含用户日程数据库。

“延期”取原 earliest 与新建议时间中的较晚者，不能把下午或晚间的在线计划改到上午立即执行。取消仍保留真实执行历史。

```bash
python3 scripts/dialogue_cognition_deploy.py check --overlay desktop-availability
```

重新部署时，仅停止 AstrBot，保持两个 QQ 实例运行：

```bash
docker stop xiaoxiaoqing-astrbot
python3 scripts/dialogue_cognition_deploy.py apply --overlay desktop-availability
docker start xiaoxiaoqing-astrbot
```

应用会保留源码恢复点。恢复只回滚源码，不覆盖聊天、记忆或日程执行记录：

```bash
docker stop xiaoxiaoqing-astrbot
python3 scripts/dialogue_cognition_deploy.py rollback backups/desktop-availability-apply-<时间> --overlay desktop-availability
docker start xiaoxiaoqing-astrbot
```

首次在线修复已在忽略目录 `backups/desktop-availability-20261103/` 保留源码及活动数据库备份，并校正两个已被错误提前、尚未执行的未来安排。已完成的活动未重做或删除。
