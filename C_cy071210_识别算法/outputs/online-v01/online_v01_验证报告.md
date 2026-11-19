# 在线逐帧入口 v0.1 交付与验证报告

- 模块：C（cy071210）｜日期：2026-11-19｜状态：**已交付并通过三层回归**
- 范围：把离线 M1 批处理改造成连续可调用的识别入口（逐帧喂入 → 九字段输出），对接 A 已就绪的实时 HTTP 入口；B↔C「解码后一帧怎么交给 C」帧级契约提案同批发出。

## 交付物

| 文件 | 内容 |
|---|---|
| `seat-detection/algos/m1_features.py` | M1 重构为逐帧状态机：`M1State(__slots__)` / `make_m1_state` / `_m1_step` / `run_m1`；离线与在线共用同一代码路径 |
| `seat-detection/algos/online_detector.py` | 在线入口 v0.1：`SeatDetector`（连续会话）+ `project_dev112p_frame`（B 单帧立方 → 1dfft 剖面，C 侧投影责任） |
| `seat-detection/tools/live_seat.py` | 驱动：`--replay` 回放回归（`--expect` 逐字节比对）／`--post-url` 逐帧串行转发 A |
| `对接事宜/2026-11-19_B-C帧级接口提案.md` | B↔C 帧级契约提案（DevFrame 结构、零掩码丢帧表达、握手 meta；待 B 确认） |
| `seat-detection/README.md`、`CAPABILITIES_AND_KNOWN_ISSUES.md` | 在线入口章节 + 能力总表更新 |

## 三层验证（全部通过）

**① 在线回放 vs 离线逐字节一致（12/12 组）**

离线基线 = `detect.py --config config/M1_dev112p_v0.2.json --emit-scores`（无 `--posture-config`，即在线 v0.1 的语义：M1 实时 + 姿态占位）；在线侧 = `live_seat.py --replay` 逐帧喂 `SeatDetector.feed_frame`，`--expect` 逐字节比对。

```
t01-p01 / p02-formal / p03-formal / p04-formal / neighbor / aisle /
p02-retest / p04-retest / chair-shift / pilot-01 / short-01 / short-02
→ 12/12: <rid>_result.csv 与 <rid>_scores.csv 全部 byte-identical
```

另：重构后的离线 `run_m1` 亦与重构前交付基线（M1+M2 路径）12/12 逐字节一致——重构零行为漂移。

**② B 单帧投影 vs 离线适配器字节等价**

`project_dev112p_frame`（在线单帧版）vs `tools/dev112p_adapter.py: chirp_mean`（离线整卷版），合成 int16 立方逐帧 `.tobytes()` 比对：

| 用例 | 结果 |
|---|---|
| 全有效 chirp | PASS 字节等价 |
| ~15% 随机缺 chirp | PASS |
| 每帧仅 1 有效 chirp + int16 极值 | PASS |
| chip0/rx1 交叉选路 | PASS |
| 零有效 chirp / chip 越界 | PASS（`DevFrameError` 守卫） |

关键实现细节：分母用 0-d int64 数组（复刻离线 complex64/int64 → complex128 → complex64 的数组-数组除法路径）；64 chirp 顺序累加与离线 `sum(axis=1)` 逐位一致。

**③ POST 转发链路冒烟（113 帧）**

本机 stub 模拟 A 端点：串行 POST（每帧等待响应），九字段键齐全、frame_id 0..112 连续、timestamp 严格递增、`posture_confidence=null`、`calibration_status=uncalibrated`、Bearer 头校验通过。

## 会话语义（v0.1）

- frame_id 0 起严格连续（跳号 = 协议错乱 → 报错；A 契约「帧号归零→重启会话」同款）
- timestamp 严格递增；帧周期显式（0.1935，不用 1/5.168 近似——F3 呼吸带频率映射会漂移）
- 零有效 chirp 帧 → `invalid_frame` 行（presence=-1），**不推进识别状态机**（NaN 剖面会永久毒化 unwrap 相位链）；提案 B 用全零掩码帧显式表达丢帧/坏帧
- 姿态占位 = 离线无 `--posture-config` 语义：posture not_applicable/unknown、calibration_status=uncalibrated
- 仅 1dfft；新会话新实例；跨会话不携带状态

## 边界与后续

- M2 姿态、B0 点云在线化未排期（回填/点云语义待设计）；capabilities 声明 `{"presence": true, "posture": false}`
- B↔C 帧级契约为**提案**（待 B 确认 DevFrame 字段、零掩码丢帧、握手 meta、传输通道）
- v1.2 presence 钉死缺陷（P0-1）不因在线化改变——在线入口交付的是**管线与一致性**，不是精度；修复（R1–R5）仍属团队决策
- 现场联调（采集电脑逐帧输出 → A 实时展示）待 B 确认契约后进行

## 复现

```powershell
# 离线基线
python detect.py --recording examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --emit-scores --out <base>

# 在线回放回归（逐字节比对）
python -X utf8 tools/live_seat.py --replay examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --out <out> --emit-scores --expect <base>

# 逐帧转发 A
python -X utf8 tools/live_seat.py --replay <npz> --config config/M1_dev112p_v0.2.json --post-url http://127.0.0.1:8766/api/frame --token <RADAR_A_FRAME_TOKEN>
```

回归驱动（12 组批量 + 投影等价 + 守卫）在 `.deepworks/tmp/m1_regress_online.py`（诊断脚本，非交付物）。
