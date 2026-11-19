# A↔C capabilities 语义收口与在线异常处理规则

日期：2026-11-19。作者：C。状态：**成文，请 A 复核**；四类规则中三条为既有文档的索引汇总，断流重连的 C 侧规则为本轮新增并已落地实现。

## 结论

响应团队评审反馈（「与 A 收口 capabilities 语义，并明确丢帧、断流重连、会话重置及 POST 失败的处理规则」），C 完成三件事：

1. **capabilities 语义成文**（三层：帧级 JSON / algorithm.json / 行级强约束），并收敛规范：**capabilities 只放 `presence`/`posture` 两个纯 bool，扩展语义一律放 notes**。
2. **四类异常处理规则索引/补齐**：丢帧、会话重置、POST 失败已有覆盖（本文汇总索引）；断流重连的 C 侧重试/对齐规则为本轮新增，已落地 `tools/live_seat.py`。
3. **C↔A 端到端预演通过**（真 `live_monitor.py --live`，非 stub）：20/20 检查项全过，覆盖全帧 POST、断流恢复（feed_interrupted→feed_resumed）、401 快败不重试、400 快败且会话存活、C 侧会话重建。

并发现一个 **A 端展示缺陷**（capabilities 渲染，见 §2.3）与一个**格式坑**（A `--live` 无法载入 detect.py 旧格式 algorithm.json，见 §2.4），C 侧已先行规避并给出方案，请 A 复核。

## 1. 范围

- 本文只覆盖 **C→A 段**（在线）。B→C 段见 `2026-11-19_B-C帧级接口提案.md`（待 B 确认）。
- A 端行为以 2026-11-19 实时入口 **v0.3.0** 为准（`live_monitor.py`、`live_feedback.py`、`offline_config.json`、`README_实时入口.md`）；权威 schema 为仓库根 `contract.py`。当日 A v0.2.0 与 v0.3.0（新增使用者视图，帧 API 无变化）C 均已端到端预演验证（§4）。
- 文内引用用函数级锚点（抗 A 改版行号漂移）；contract.py 行号为当日版本实测核对。

## 2. capabilities 语义收口

### 2.1 帧级：严格九字段

- A `live_monitor.py` 对 POST body 做严格 set 相等校验（多键/少键 → 400），走 `contract.parse_result_row` 逐字段校验（枚举值、置信度区间、跨字段一致性）。
- C 侧 `SeatDetector.feed_frame()` 返回值即九字段 dict（`algos/online_detector.py: RESULT_COLUMNS`）；`live_seat.py` POST 时仅序列化这九个键。
- **规则**：任何扩展字段禁止出现在帧 JSON 里。预演已验证 A 全帧接受（349 帧）。

### 2.2 algorithm.json：`capabilities` 只放两个纯 bool

- `contract.validate_algorithm`（contract.py L185-186）要求 `capabilities` 为 dict 且 `presence`/`posture` 为 bool；**额外键不拒绝**。
- 但 A `live_monitor.py` 的 `snapshot()` 把 capabilities 渲染为「已启用能力键名列表」：`[k for k, v in caps.items() if v]`——**任何 truthy 扩展键都会被当成能力展示**。detect.py 离线 algorithm.json 里的 `posture_semantics`（一段说明字符串）会污染该列表。
- **收口规范（C 已执行）**：
  - `capabilities` = `{"presence": <bool>, "posture": <bool>}`，仅此两键；
  - 语义说明文本一律放 `notes`（contract 不校验 notes 内容）；
  - C 侧 `SeatDetector.capabilities()` 已改为纯 bool，语义挪入 `capabilities_semantics()`（仅进诊断件 `<rid>_session.json`，A 不读）。
- **建议 A 侧后续修复（可选）**：渲染时只取 bool 类型的已知能力键。C 已先行收敛，此缺陷不再触发。

### 2.3 `posture=false` 是行级强约束，不只是展示

- `contract.parse_result_row`（L268-271 附近）按 capabilities 交叉校验每一行：声明 `posture=false` 时，所有行必须 `calibration_status=uncalibrated` 且 `posture ∈ {unknown, not_applicable}`；`quality≠ok` 行必须全空+unknown。
- 在线 v0.1 的输出（presence=0 → not_applicable；有人 → unknown；cal=uncalibrated）天然满足。**含义**：A 载入的 algorithm.json 声明必须与 C 会话真实能力一致——在线会话配 `posture=false` 声明，而不是沿用带 M2 的离线声明。
- **因此在线会话不能用 detect.py 生成的旧 algorithm.json**（它声明的是离线管线），见下。

### 2.4 A `--live` 需要 contract 新格式 algorithm.json（格式坑与方案）

- **事实**：detect.py 生成的离线 algorithm.json 是旧回放格式——`smoothing` 键（contract 要求 `temporal_smoothing`）、无顶层 `thresholds`。A 的旧格式转译只在「名义时间 + 回放」路径生效；`--live` 走 `validate_algorithm` 严格校验（ALGORITHM_KEYS 全集），**直接载入旧格式会启动失败**。
- **方案（C 已实现）**：`tools/live_seat.py --emit-algorithm` 生成 `<rid>_algorithm_online.json`——contract v1 全键（schema_version/recording_id/algorithm_version/parameter_version/capabilities/config/thresholds/window_s/temporal_smoothing/model_version/calibrations/notes），capabilities 纯 bool（§2.2），阈值/窗/滞回文案取自会话状态机（t_on/t_off/win_gate/confirm），`parameter_version = md5_8(在线生效参数)+online-v0.1`。
- 已通过 `contract.validate_algorithm` 实测校验 + 真 A `--live` 启动实测载入。
- detect.py 离线格式**本轮不改**（避免破坏已交付基线的可复现性）；离线三件套的 A 端离线导入路径不受影响。统一格式可在 v1.2.1 算法主线一并处理。

## 3. 四类异常处理规则

### 3.1 丢帧（B→C→A）

| 环节 | 规则 | 索引 |
| --- | --- | --- |
| B→C | 无有效数据的帧号上发全零 `valid_chirps` 帧保持 frame_id 连续（显式信号，区别于静默跳帧） | B-C 帧级接口提案 关键决策 2/3（待 B 确认） |
| C | 输出 `invalid_frame` 行（presence=-1、target_count=-1、conf 空）**不推进识别状态机**，记 warning | `algos/online_detector.py: feed_dev112p`（DevFrameError 语义） |
| C→A | invalid_frame 行合法通过 `parse_result_row`（quality≠ok 分支要求全空+unknown，恰是 C 的输出） | contract.py L266-267；预演 T1 全帧接受 |
| 越界 | frame_id 跳号 = 协议错乱：C `SeatDetector` 直接 raise；A `validate_position` 400 拒收 | online_detector `_check_frame_id`；live_monitor `validate_position` |

### 3.2 断流重连

**A 侧（既有，索引）**：`live_feedback.py: observe_silence/accept` + `offline_config.json`

- 超过 `data_gap_s=2` 未收到新帧 → `feed_interrupted`：**冻结计时、把座位移出可用列表**；
- 持续超过 `review_after_s=30` → `feed_review`：提示检查采集与算法进程；
- 恢复收到新帧 → `feed_resumed`：**按当前证据重新确认占用，不回补、不追认断流期间的任何状态**。

**C 侧（本轮新增，已落地 `live_seat.py: post_row`）**：

- 网络层失败（URLError/超时）→ **指数退避重试同一帧**（默认 `--retry-max 8` 次、`--retry-base 1s`：1s,2s,4s,…；帧数据不丢）；
- 每次重试前经 `GET /api/state` 对齐（A 契约「不确定请求是否成功时先查 `state.frame_id`」）：若 A 实际已接受该帧（响应丢失场景）则**不重发**，避免重复帧 400；
- 重试耗尽 → 中止会话并明确报错（**不静默丢帧**）；恢复须按会话重置规则处理（§3.3）。

**预演证据（T2）**：喂 174 帧 → 静默 3.2s（>2s 阈值，A 由轮询驱动记 `feed_interrupted`）→ 继续喂完 → `feed_resumed`，A 全 349 帧连续接受（`state.frame_id=348`），会话无断裂。

### 3.3 会话重置

- **基准规则（既有）**：帧号归零/新录制/采集重启 = 新会话 → **重启 A 与 C 会话**（A README_实时入口「帧号归零」节；B-C 提案 §5 会话边界）。C 侧新会话 = 新建 `SeatDetector`（frame_id 从 0 起，跳号 raise），不跨会话携带状态。
- **边界情形（本轮预演 T3/T4 发现并验证）**：HTTP 错误（如 401）导致 C 中止时，若 **A 一帧未收**（`GET /api/state` 确认 `state.frame_id` 为空），C 重建会话（新 SeatDetector 从 frame 0）即可重新对齐，**A 无需重启**；若 A 已收部分帧，则双方都重启。修复操作（换 token 等）后重跑 `live_seat.py` 自然满足此语义（进程重启即新会话）。

### 3.4 POST 失败

| 情形 | 规则 | 索引/实现 |
| --- | --- | --- |
| HTTP 状态错误（400/401/403/…） | **不重试，立即中止**。400 按 A 契约「修正当前帧」（数据 bug，重试只会重复同样错误）；401 换 token 后按 §3.3 边界情形重跑 | A README_实时入口；`live_seat.py: post_row`（HTTPError 分支）；预演 T3/T4：0.01s/0.02s 快败 |
| 网络层失败 | 见 §3.2 C 侧规则（退避重试 + GET state 对齐 + 耗尽中止） | `live_seat.py: post_row` |
| 响应回执异常 | `accepted_frame_id` 与所发帧号不符 → 会话错乱，立即报错 | `live_seat.py: post_row`（新增回执校验） |
| 串行约束 | 每帧等响应后再发下一帧（A 契约） | A README；live_seat 主循环 |

## 4. C↔A 端到端预演（真 A，非 stub）

脚本：C 工作区 `.deepworks/tmp/live_e2e_a.py`（pilot_01，349 帧，M1 v1.2 配置）。结果 **20/20 全过**（对 A v0.2.0 与 v0.3.0 各跑一轮；v0.3.0 仅新增使用者视图资产路由，帧 API 无变化）：

| 组 | 验证点 | 结果 |
| --- | --- | --- |
| P0 | `--emit-algorithm` 生成 contract 新格式文件；capabilities 纯 bool | PASS（`contract.validate_algorithm` 亦实测通过） |
| T1 | live_seat 全帧串行 POST 349 帧：A 全收（state.frame_id=348）、capabilities 渲染 == `["presence"]`、无 feed_* 异常事件、phase=receiving | PASS |
| T2 | 断流 3.2s：`feed_interrupted` + `feed_resumed` 均记录；会话连续（不丢帧、帧号不断裂） | PASS |
| T3 | 错 token → HTTP 401：立即失败（0.01s，无重试退避）；A 一帧未收 | PASS |
| T4 | 跳帧（frame_id=99）→ HTTP 400：立即失败（0.02s，无重试）；修正后续帧仍被接受（会话存活，state.frame_id=5） | PASS |

## 5. 三端现场联调启动序列（B 确认后）

1. B 提供会话 meta（含 `radar_config.nominal_frame_period_s`、`installation`）。
2. C：`python -X utf8 tools/live_seat.py --replay <会话样例或回放> --config config/M1_dev112p_v0.2.json --emit-algorithm`（或首跑时自动生成）→ 产出 `<rid>_algorithm_online.json`。
3. A：`python live_monitor.py --live --meta <rid>_meta.json --algorithm <rid>_algorithm_online.json`（token 经环境变量 `RADAR_A_FRAME_TOKEN`）。
4. C：`python -X utf8 tools/live_seat.py --replay ... --post-url http://127.0.0.1:8766/api/frame --token <token>`（实况时由 B→C 帧级通道喂 `feed_dev112p`，POST 链路相同）。
5. 现场动态标定协议按 A README 留待联调时定。

## 6. 请 A 复核清单

1. §2.2 capabilities 纯 bool 规范是否认可；`snapshot()` 渲染缺陷是否列入 A 修复项（C 已规避，非阻塞）。
2. §2.4 在线会话 algorithm.json 采用 `_algorithm_online.json`（C 生成）方案是否认可。
3. §3.2/§3.3/§3.4 规则与 A 契约的理解是否一致；`data_gap_s=2`/`review_after_s=30` 对 5.168 Hz 现场链路是否合适。
4. §3.3 边界情形（A 未收帧时 C 单方重建即可对齐）是否认可。
5. §5 启动序列在 A 侧启动脚本（`启动座位监测.cmd`）里是否需要固化。
