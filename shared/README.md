# 共享离线接口 v1

唯一可执行定义：[contract.py](../contract.py)。B/C/A 不分别复制这份定义。

- B：timestamps float64[N]、frame_ids int64[N]、raw_data numeric[N,...]；数组按实际载荷保留维度、dtype 和值。meta 记录完整来源、安装、配置、轴和单位。
- C 固定表头：frame_id,timestamp,presence,presence_confidence,target_count,posture,posture_confidence,calibration_status,quality。
- 时间是本次采集开始后的非负相对秒，严格递增。C 对应原始时间保持 float64 往返精度，不排序、不填补或删除帧。
- quality 非 ok 时必须未知；未知不能转无人。人数只针对目标座位区域。
- calibration.valid_from 不早于参考段结束；标定失效、返回重标定和模型因果性须记录并由上下游共同验证。
- D labels 独立；A 只以 C 证据及相应配置推进行为。

下一项任务：依据实际 DEV112P 协议补充经过核实的数据级别、各轴和 IQ 表示示例；格式改变时升级 schema，并新增跨模块兼容测试。

验收：python selftest.py；修改此接口需 B/C/A/D 共同检查影响，所有同段帧数、帧号和时间逐行对应。
