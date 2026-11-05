# A · 应用与业务

**当前实现**：[offline_state_machine.py](../../offline_state_machine.py)，由 [radar_offline.py](../../radar_offline.py) 的 run-a 命令调用。不是已实现的网页或实时服务。

输入：同一连续单座采集的 result.csv、meta.json、algorithm.json 和 config.json；原始 NPZ 仅用于逐帧对应校验。

输出：output/recording_id/run_id 下 timeline.csv、events.csv、run.json。处理占用确认、会话、等待与释放、姿态确认/累计、一次事件一次提醒、冷却、数据缺口和人工复核。

下一项任务：基于已生成结果做离线查看页面，正确区分 confirmed/pending/review/unknown；不将未知当空闲，不在 EOF 追加释放。

验收：现有状态机测试全通过；同输入参数两次业务 CSV 一致；在离座返回时沿用会话、要求新参考；不同播放速度不影响提醒/释放时间。
