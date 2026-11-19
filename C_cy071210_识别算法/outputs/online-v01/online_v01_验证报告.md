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

## 2026-11-19 补录：capabilities 收口 + C↔A 端到端预演（真 A，非 stub）

响应组员反馈完成的两项收口（详见镜像 `对接事宜/2026-11-19_A-C_capabilities语义与在线异常处理.md`，待 A 复核）：

1. **capabilities 语义收口**：`SeatDetector.capabilities()` 改为纯 bool
   `{"presence": true, "posture": false}`（语义文本外移 `capabilities_semantics()`，仅进诊断件）；
   `live_seat.py --emit-algorithm` 生成 **contract v1 新格式** `<rid>_algorithm_online.json`
   （capabilities 纯 bool、顶层 thresholds、temporal_smoothing、parameter_version=md5_8+online-v0.1），
   通过镜像 `contract.validate_algorithm` 实测校验——detect.py 离线 algorithm.json 为旧回放格式，
   A `--live` 严格校验不收，在线会话必须用本文件。
2. **POST 失败/断流 C 侧规则落地**（`live_seat.py post_row` 重写）：HTTP 状态错误不重试即中止；
   网络错误指数退避重试（`--retry-max 8`/`--retry-base 1s`），重试前 `GET /api/state` 对齐
   防重复帧；耗尽中止不静默丢帧；回执 `accepted_frame_id` 与帧号不符即报错。

**端到端预演（pilot_01，349 帧，M1 v1.2 配置；脚本 `.deepworks/tmp/live_e2e_a.py`）20/20 全过**：

| 组 | 验证点 | 结果 |
| --- | --- | --- |
| P0 | `--emit-algorithm` 生成 contract 新格式；capabilities 纯 bool | PASS |
| T1 | live_seat 全帧串行 POST 349 帧：A 全收（state.frame_id=348）、capabilities 渲染 == `["presence"]`、无 feed_* 事件、phase=receiving | PASS |
| T2 | 断流 3.2s（> A data_gap_s=2）：`feed_interrupted` + `feed_resumed` 均记录；会话连续不丢帧 | PASS |
| T3 | 错 token → HTTP 401 快败（0.01s，无重试退避）；A 一帧未收 | PASS |
| T4 | 跳帧（frame_id=99）→ HTTP 400 快败（0.02s，无重试）；修正后续帧仍被接受（会话存活） | PASS |

改造后 12 组在线回归重跑 **ALL PASS**（逐字节一致，零漂移）。

## 复现

```powershell
# 离线基线
python detect.py --recording examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --emit-scores --out <base>

# 生成 A --live 启动文件（contract v1 新格式）
python -X utf8 tools/live_seat.py --replay examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --out <dir> --emit-algorithm

# 在线回放回归（逐字节比对）
python -X utf8 tools/live_seat.py --replay examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --out <out> --emit-scores --expect <base>

# 逐帧转发 A（--retry-max/--retry-base 为网络错误退避重试参数）
python -X utf8 tools/live_seat.py --replay <npz> --config config/M1_dev112p_v0.2.json --post-url http://127.0.0.1:8766/api/frame --token <RADAR_A_FRAME_TOKEN> [--retry-max 8 --retry-base 1.0]
```

回归驱动（12 组批量 + 投影等价 + 守卫）在 `.deepworks/tmp/m1_regress_online.py`（诊断脚本，非交付物）。
