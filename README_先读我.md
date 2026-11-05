# DEV112P 离线接口 v1 工具包

按《离线接口规范_v1.pdf》和《CSV格式示例.pdf》重新实现。当前雷达未连接、没有真实数据，所有附带演示均标为 **synthetic**。代码验证的是文件接口和业务时间规则，尚未实现 DEV112P 透传解析、人体存在识别或坐姿分类模型。

## 现在怎么用

1. 将代码 ZIP 完整解压到电脑的普通文件夹，保留全部文件。
2. 双击 `run_demo.cmd`。它在 `DEMO_ONLY/demo_随机编号/` 新建模拟采集及 A 输出，并生成 `DEMO_ONLY_transfer.zip`；不会覆盖已有演示。
3. 查看 `output/采集编号/demo_run_01/timeline.csv`、`events.csv` 和 `run.json`。CSV 可用文本编辑器查看；时间是数据中的相对秒数。
4. 将生成的 `DEMO_ONLY_transfer.zip` 整包离线交给组员。组员先按 `README_组员离线读取.txt` 校验，再读取。现在传出的只能是联调模拟数据。
5. 正式采集前，获得真实输出协议、已解码载荷和真实逐帧时间，填完整 `meta_template.json`。目前不提供猜测串口参数或把透传字节当 ADC 的代码。

本机启动器优先使用已存在的 Codex Python，然后尝试系统 `py` / `python`。不自动安装或联网。依赖 Python 3.10+、NumPy；开发验证环境为 Python 3.12.14、NumPy 2.3.5。接收方不需要 Codex，但需事先具备 Python/NumPy。ZIP 不是独立可执行程序。

## 对应分工

| 角色 | 输入 | 输出与命令 |
|---|---|---|
| B | 已按实际协议解码的固定形状数值数组、真实时间、完整元数据 | `import-decoded` 保存 NPZ/JSON；`validate --stage b`；`pack --stage b` |
| C | B 的完整采集目录 | 自行实现因果算法，使用 `contract.write_result` 输出固定九列，补全 algorithm.json；`validate --stage c` |
| A | C 的结果、meta、algorithm 与明确 config | `run-a` 输出 timeline/events/run；原始数组只参与对应校验 |
| D | 独立观察标注 | labels.csv 单独保存；本工具不将它作为推理或业务输入 |

此接口版本每目录只处理一个目标座位。多座位项目应分别采集/处理并在后续版本约定多座位汇总；本工具不宣称一块雷达已能分离多个隔板座位。

## 命令示例（在解压目录打开终端）

```text
.\run_python.cmd selftest.py
.\run_python.cmd radar_offline.py demo --output DEMO_ONLY/new_demo
.\run_python.cmd radar_offline.py validate DEMO_ONLY/new_demo/20261103_PDEMO_S01_lifecycle_01 --stage c
.\run_python.cmd radar_offline.py run-a DEMO_ONLY/new_demo/20261103_PDEMO_S01_lifecycle_01 --config DEMO_ONLY/new_demo/demo_config.json --output output --run-id rerun_01
.\run_python.cmd radar_offline.py pack DEMO_ONLY/new_demo/20261103_PDEMO_S01_lifecycle_01 --stage c --output DEMO_ONLY/delivery.zip
.\run_python.cmd verify_transfer.py DEMO_ONLY/delivery.zip
```

同名目标已存在会报错，不覆盖采集、ZIP 或 A 的运行目录。C 写文件时也采用独占创建。请完整写入、关闭后再交付下游，不并行修改上游文件。

正式已解码数据的导入示例（这些输入文件现在尚不存在）：

```text
.\run_python.cmd radar_offline.py import-decoded --raw decoded_payload.npy --timestamps measured_relative_seconds.npy --meta completed_meta.json --output recordings
.\run_python.cmd radar_offline.py validate recordings/20261103_P01_S01_sit_01 --stage b
.\run_python.cmd radar_offline.py pack recordings/20261103_P01_S01_sit_01 --stage b --output B_delivery.zip
```

可加 `--device-frame-ids device_frame_ids.npy` 保留设备计数。导入不改变载荷 dtype、轴、值，不滤波、删帧或依据标称帧率补造时间。只接受已解码 `.npy`，不接受未知二进制或可变长 object 点云。

## 数据契约

- NPZ 必含 `timestamps: float64[N]`、`frame_ids: int64[N]`（0 到 N−1）及 `raw_data: numeric[N,...]`；实际几维就保存几维。可另含整数 `device_frame_ids[N]`。
- 时间为 `seconds` / `recording_start`，非负且严格递增。重复、倒序、空值不修复；报文件名及 CSV 行号或数组行号（从 1 开始）。时间来源须由采集人员据协议核实，软件无法凭数值鉴定真实时间或单位是否谎填。
- meta 必填字段见模板；`raw_shape`/`raw_dtype` 必须匹配实际数组，`axis_order` 每维一个名称、首维 `frame`。数据级别、单位、IQ表示必须在正式交付前明确；未知固件/采样/扫频/通道配置保留 null。`subject_id` 使用匿名编号或 null；无被试的文件名可用 PNA。
- result 固定表头：`frame_id,timestamp,presence,presence_confidence,target_count,posture,posture_confidence,calibration_status,quality`。N 帧严格对应 N 行；用 Python `repr(float(timestamp))` 写回原始双精度时间，不能自行四舍五入。
- presence: −1/0/1；姿态: reference/forward/backward/unknown/not_applicable；标定: uncalibrated/calibrating/valid/invalid；质量: ok/low_signal/invalid_frame。未知置信度写空字段，不写文字“空”、NaN 或零代替未知。
- 无人必须 count=0、姿态不适用；未知 presence 必须 count=−1、姿态未知、存在置信度空；质量非 ok 必须全部未知、两置信度空。有人但标定无效只输出未知姿态。置信度范围 [0,1]，不假设已校准概率。
- C 尚无坐姿算法时，能力 posture=false、标定 uncalibrated，仍保留全部九列。A 不拿训练/评价 labels 推断姿态。

## C 的算法说明与标定

`algorithm.json` 必含 schema_version、recording_id、algorithm_version、parameter_version、`capabilities:{presence:bool,posture:bool}`、config、thresholds、window_s、temporal_smoothing、model_version（可 null）、calibrations。

每条标定记录包含 calibration_id、seat_id、start_timestamp、end_timestamp、valid_from_timestamp、invalid_from_timestamp（未失效为 null）、invalidation_reason（未失效可 null）。需 `start <= end <= valid_from`，有效区间不得重叠。有效状态的 CSV 行必须有当前生效的标定记录。C 应使用明确指定、无姿态真值标签参与的参考段，推荐 10–15 秒；不能从未来帧倒填“已有效”。

示例 `radar_offline.synthetic_algorithm()` 只是写字段的模板。演示证据按时间脚本生成，**没有根据 raw 推理**。校验器能检查行对应、枚举、标定生效时间，不能从结果文件证明真实模型的因果性、标定来源或识别性能；C 的实现仍需代码审查与实测。

## A 的时间规则与实现选择

完整参数记入每次 run.json。`config_template.json` 的 `data_gap_s=null` 故意不能运行：必须依据真实输出周期设定。演示为 10 Hz，**仅演示**设置 gap=0.5 秒；两个置信度门限 null 表示禁用，不冒充已验证阈值。

- 初态 unknown；有人连续 2 秒确认入座，无人连续 3 秒确认空闲/进入离座等待。按“当前时间减证据首帧时间”确认，不按帧数计算。
- 离座等待从无人确认时起计；仅相邻、已确认且有效的无人间隔累加 60 秒后释放。短未知暂停，恢复后重新确认才续计。数据缺口超过阈值，整段不计时；恢复并确认无人后从新的等待计时开始。
- 未知、低置信度、质量异常保留最后确认状态，pending=1。连续未知达 30 秒输出 review_required，不能计入可用空位；有效证据重新确认后清除复核标记。仅姿态未知不影响占用。
- 入座创建会话，等待期间返回沿用同一会话，但重置姿态并要求新个人参考。实现采取保守规则：参考段 start 必须不早于 A 本次确认入座/返回的时刻；旧标定即使 C 仍写 valid，A 的有效状态也降为 invalid。这条策略写入 run.json，C 的标定计划需与之协调。
- 前倾/后靠连续稳定 2 秒后才开始累计，累计 10 秒提醒一次。切换姿态重新稳定并清零；参考连续 3 秒清零；未知立即暂停、连续严格超过 3 秒清零。短暂未知恢复后重新稳定，暂停及稳定等待期间不累计。
- 同一持续事件提醒一次。新姿态事件、返回或新会话都不能绕过本文件上次提醒后的 60 秒冷却；每个新文件独立重置状态。
- 文件结束立即停止，不补帧、不补足释放时间。未释放会话保留 complete=false；不发声，只输出 reminder 事件。
- A 对 result/meta/algorithm/config 记录 SHA-256，原始 NPZ 只校验帧时间对应。重复相同输入/参数会产生相同 timeline/events；run_id、创建时间等运行元数据除外。程序没有回放倍速参数或 sleep，显示速度不会进入业务计算。

timeline 与 events 的固定表头按原规范；不适用字段为空。释放事件保留原会话编号，释放后的 timeline 不再携带已结束会话编号。run.json 保存各会话的起止、是否完成和 EOF 是否确认空闲。

## 离线交付和局限

`pack` 只收录当前阶段的固定接口文件和接收辅助程序，不带 labels。交付 ZIP 自带 SHA-256 清单；校验用于发现损坏，不提供发送者认证。软件不自动发送信息。

当前不包含串口驱动、板卡固件、授权工具、实际安装参数、真实采集/分类。拿到这些后，应先完成 B 的一次单座真实闭环，再接 C 实测算法；不能把附带模拟 raw 当作 DEV112P 的 ADC、距离谱或点云。
