# 能力与已知问题说明（CAPABILITIES_AND_KNOWN_ISSUES）

- 模块：C（cy071210）｜`seat-detection/` ｜ 更新：2026-11-21
- 一句话结论：**v1.2.1（v1.2 的修复版）已交付：开场空座误报 100%→1%、邻座/过道全空组 100%→0%、走远判空 0/7→3/7；v1.2 保留原样（冻结缺陷）以便对照。** 静坐保持 95%（唯一损失是 12 s 完全静止段，见 §4.3）。姿态仍为显式拒识。

## 0. v1.2.1 修复版（2026-11-21，分支 `feat/c-v1.2.1`）

| 能力 | 状态 | 依据 |
|---|---|---|
| 奇偶 chirp 分链（R1） | ✅ 转换层 `--parity split`；在线投影同口径逐字节一致 | `tools/dev112p_adapter.py`；`tools/selftest_v121.py` |
| 开场空座标定（R2） | ✅ ≥10 s 有效帧（≥40 帧）后才启用占用判断，标定期 presence=0 | `config/M1_dev112p_v1.2.1.json` |
| 离座判空（R3） | ✅ Bin1 微动回到空座带 5 s + F1 稳定性否决 → 判空并重建快照 | `algos/m1_v121.py` |
| 验收结果（9 组真实录制） | 开场误报 240/240→3/240；静坐保持 95%；全空组 532/532→0/532；走远判空 3 次 9.0–10.1 s | `../outputs/v1.2.1/v1.2.1_comparison.md` |
| 在线/离线一致性 | ✅ 逐字节一致（result.csv + scores.csv） | `tools/live_seat.py --expect` |

设计要点：F1 由绝对 dB 改为**泄漏归一残差**（`|X|/|X0|`，消除 2.9× 基数差与热漂移）；
判空由 **Bin1 瞬时变化（10 s 去趋势幅度 std/mean，按标定空座噪声底归一）** 驱动，
F1 稳定性在释放时刻作否决；噪声底/背景快照只在无人态更新或判空后重建。

复现：`python detect.py --recording examples/real_v121/<rid>_1dfft_parity_raw.npz --config config/M1_dev112p_v1.2.1.json --emit-scores`；
对照表：`python tools/eval_v121.py`；自测：`python tools/selftest_v121.py`。
完整报告：`../outputs/v1.2.1/v1.2.1_fix_report.md`。

---

## 1. 能力总表（v1.2 基线）

| 能力 | 状态 | 依据 |
|---|---|---|
| presence 管线（背景更新/特征提取/阈值/滞回，implemented as program） | ✅ 已实现为程序，从 `raw.npz + meta.json` 直接生成结果 | §2；`detect.py` + `algos/m1_features.py` |
| presence 在真实 T01 数据上的可用性 | ❌ **不可用（v1.2 钉死缺陷）**，11/12 组空场误报 93.8–98.7%、离座零释放 9/9 | §4.1；round-2 缺陷报告；开发集验证报告 |
| 姿态管线（参考标定/特征/质量门/拒识，implemented as program） | ✅ 已实现（M2 v0.1 gated baseline） | §3；`algos/m2_posture.py` |
| 姿态分类在当前数据上输出 | ❌ **11/12 组拒识（unknown/invalid）**；P01 过门但方向不可验证 | §4.2；posture_v01_diagnostics.md |
| 合成数据自测（G1） | ✅ 20/20 PASS（合成域） | README「G1 自测流程」 |
| 批量预测 + 契约校验 | ✅ 12/12 组 result.csv C1–C8 PASS | §5；`algos/selfcheck.py` |
| **在线逐帧入口（v0.1）** | ✅ 已实现：`SeatDetector` 逐帧喂入，12 组回放与离线**逐字节一致**；B 单帧投影与离线适配器**字节等价**；C↔A 端到端预演（真 `--live`）**20/20 通过**；`--emit-algorithm` 产出 contract 新格式启动文件（`contract.validate_algorithm` 实测通过） | §2.5；`algos/online_detector.py`；`tools/live_seat.py` |
| 在线姿态 | ❌ v0.1 为接口占位（not_applicable/unknown + uncalibrated，与离线无 `--posture-config` 一致）；M2 在线化（回填语义）未排期 | §2.5 |
| 在线 2dfft/B0 | ❌ v0.1 仅 1dfft（M1）；`SeatDetector` 显式拒收 2dfft | §2.5 |
| B↔C 帧级契约 | ⏳ 提案已发（DevFrame/零掩码丢帧/握手 meta），待 B 确认 | 仓库 `对接事宜/2026-11-19_B-C帧级接口提案.md` |
| dsraw 直读 | ❌ 不支持（待 DS10012RN 协议，不猜测未知格式） | detect.py 显式报错 |
| bin-距离标定 | ❌ 未标定（region 用几何先验名义值） | metadata_gaps.md |

## 2. M1 presence 管线（程序落实，对应组员要求①）

对每帧依次执行（`algos/m1_features.py`，无任何人工中间步骤）：

1. **背景更新**：EMA（α=0.2）**仅对 presence=0 帧更新**，presence=1 冻结背景（防人被吸收入背景）；
2. **特征提取**（4 s timestamp 窗，不 resample）：F1 区域残差能量（相对冻结背景）、F2 主 bin 相位 std、F3 主 bin 呼吸频段 [0.1,0.5] Hz 能量占比、F4 帧间能量跳变（预留权重 0）；
3. **阈值与滞回**：加权分数（0.45/0.30/0.25）→ T_ON=0.5 / T_OFF=0.35 滞回 + confirm=5 帧防抖 + 静态门 f23_floor=0.10（有人态下 (F2n+F3n) 均值过低强制释放，防"人走包留"）；
4. **质量门**：`invalid_frame`/`low_signal` → presence=-1（不冒充 0/1）；
5. **契约自检**：写盘后立即跑 selfcheck C1–C8，FAIL 即非零退出。

v1.2 快照（`config/M1_dev112p_v0.2.json`）为团队冻结配置，与 v1.1 的差异是 f1 绝对 dB 映射按真实数据重标。

## 2.5 在线逐帧入口 v0.1（2026-11-19 交付）

`run_m1` 重构为逐帧状态机（`make_m1_state`/`_m1_step`，**离线 run_m1 与在线 SeatDetector 共用同一代码路径**），其上交付连续会话入口：

- **会话语义**：`SeatDetector` 一次会话一个实例；frame_id 0 起严格连续（跳号=协议错乱即报错，A 契约同款）；timestamp 严格递增；`feed_frame`（已投影剖面）/`feed_dev112p`（B 单帧立方，C 侧 `project_dev112p_frame` 投影）→ 九字段行 dict。
- **无效帧语义**：零有效 chirp 帧输出 `invalid_frame` 行（presence=-1、target_count=-1），**不推进识别状态机**（NaN 剖面会永久毒化 unwrap 相位链）；建议 B 用全零掩码帧显式表达丢帧/坏帧（提案待确认）。
- **能力占位**：posture=not_applicable（无人）/unknown（有人）、calibration_status=uncalibrated——与离线不带 `--posture-config` 逐字段一致；在线 capabilities 声明 `{"presence": true, "posture": false}`。
- **验证证据**（四层）：① 12 组 T01 逐帧喂入 vs 离线 detect.py（同 v1.2 配置、无姿态分支）result.csv + scores.csv **逐字节一致**；② 单帧投影 vs 离线适配器 `chirp_mean` **字节等价**（全有效/15% 缺 chirp/单有效 chirp/int16 极值/chip0-rx1 交叉选路 + 零 chirp 与越界守卫）；③ C↔A **端到端预演（真 live_monitor.py --live，非 stub）20/20**：全帧 POST（349 帧全收、capabilities 渲染==["presence"]）/ 断流恢复（feed_interrupted→feed_resumed，会话不丢帧）/ 401 快败不重试 / 400 快败不重试且会话存活 + C 侧会话重建；④ `--emit-algorithm` 产出的 `<rid>_algorithm_online.json` 通过镜像 `contract.validate_algorithm` 实测校验（A --live 启动实测载入）。
- **在线异常规则**（与 A 收口，2026-11-19）：HTTP 状态错误不重试即中止（400 修当前帧/401 换 token）；网络错误指数退避重试（`--retry-max 8`/`--retry-base 1s`），重试前 `GET /api/state` 对齐防重复帧；耗尽中止不静默丢帧；回执 `accepted_frame_id` 与帧号不符即报错；A 未收帧时 C 单方重建会话即可对齐。capabilities 为纯 bool（扩展语义只进 notes），A --live 启动必须用 `_algorithm_online.json`（detect.py 离线 algorithm.json 是旧回放格式，A --live 严格校验不收）。详见镜像 `对接事宜/2026-11-19_A-C_capabilities语义与在线异常处理.md`（待 A 复核）。
- **边界**：仅 1dfft；帧周期必须显式（会话 meta `nominal_frame_period_s`=0.1935，不用 1/5.168 近似——F3 频率映射会漂移）；回放回归用 `median(diff(ts))` 对齐离线。
- **v1.2.1 在线**：`feed_dev112p` 按 `chirp_parity` 投影（`project_dev112p_frame(..., parity=True)`，与适配器 `chirp_split` 逐字节一致）；meta 3 维 `raw_shape` 或 config `chirp_parity.enabled` 自动启用；回放与离线仍逐字节一致（`tools/selftest_v121.py` 覆盖投影一致性）。

## 3. M2 姿态管线 v0.1（gated baseline，对应组员要求④）

1. **参考标定**：presence=1 稳定 10s 后取 50 帧连续窗统计（能量重心/能量和）；
2. **特征提取**：每帧 region±2 bins（排 bin0 泄漏）的能量重心与能量和；
3. **对比度质量门**：参考窗 vs 紧随窗重心差 < 2.0×合并 std → **拒识**（posture=unknown、calibration_status=invalid，量化原因写入 algorithm.json notes）；
4. **分类（过门才启用）**：Δcentroid ≥ ±0.02 bin → forward/backward；能量比越界 [0.5,2.5] → unknown。

接口词表：posture ∈ {reference, forward, backward, unknown, not_applicable}；calibration_status ∈ {uncalibrated, calibrating, valid, invalid}。`--posture-config` 缺省不加载（presence 交付保持 v1.2 可比）。

## 4. 已知问题（按严重度）

### P0-1 presence 钉死（v1.2 结构性缺陷；**v1.2.1 已修复**，v1.2 配置与输出保留作对照）

- 现象：11/12 组 first1 落在 t=1.16–2.13s（真实首入座 17–21.6s），空场误报 93.8–98.7%，9/9 离座事件零释放；P01 唯一"通过"（0% FP、入座确认 +4.45s provisional）；
- 根因（三因素叠加，量化见 round-2 报告 §2）：f1 绝对阈值在 P01 冷机基数 3538 上开环标定（[31.6,224] counts），热机基数 7000–10300 下放大 2–2.9×；录制内漂移 −1.9~−6.2%/min（快初始衰减 + 慢线性两级）打穿阈值；frame-0 奇偶配比偏置 +139（tx_polling=1 奇偶差 11×）污染种子帧（P01 恰好 25/25 零偏置=幸存）；
- P01"通过"是**三重幸存者偏差**（冷机低基数 + 零偏置种子 + 无长空场段触发失败路径）——不能作为能力证据；
- 静坐者真实签名在**方差层**（空座 std 0.1% vs 静坐 0.2–1.0%，差 2–8×），f3/f4 已看到人但权重合计 0.2；
- 修复方向 R1（adapter 奇偶平衡）+ R2（f1 尺度归一）+ R3（方差/动态提权）+ R5（多强度重标定）——**属团队决策，本任务配置冻结**；trim12 what-if 已证明单修种子不够。
- **v1.2.1 落地情况**（2026-11-21）：R1/R2/R3 已实现并验收（开场误报 100%→1%、全空组 100%→0%、走远判空 0/7→3/7）；R5 未做（需要多强度补采）。v1.2.1 仍存在的限制见 §4.3 与 `../outputs/v1.2.1/v1.2.1_fix_report.md` §5。

### 4.3 v1.2.1 残留限制（如实声明）

- **完全静止 ≥12 s 可能误判空座**：T01_P04_R01 末段 114–126 s（后靠不动）Bin1 微动落到 m≈1.0（与空座重叠），静坐保持 85%；组员 484 帧里「静坐/空座」Bin1 变化比 5.65×，该阈值在那边余量更大；
- **离座但人仍在波束内**时不判空：P02（12.3 s 离座）、P02_retest（16.6 s 离座）走远过程 m 一直高于 1.45，录制内未回到 0；判空延迟 = 人离开波束时间 + 5 s 确认，不是固定值；
- 入场延迟 0.1–7.7 s（取决于受试者静止程度），未做亚秒级优化；
- 邻座/过道 0 误报依赖当前入场门槛（m≥4.0 + 8 帧确认）；该两组目标座 Bin1 空座幅度只有 1305/1383 counts（P04 为 5501），门槛再降会重新引入误报。

### P0-2 姿态类别在当前数据不可分（数据层限制）

- 三条特征路线量化否决（幅度谱/多普勒不对称/帧差多普勒）：泄漏电平高人体信号 1–2 个数量级，类间差 0.001–0.01 bin ≈ 段内噪声；
- M2 v0.1 因此是**拒识管线**：11/12 组门拒（unknown+invalid，原因在 notes）；P01 过门但 4/5 事件窗口翻转响应无法验证方向（draft 无方向标签 + 单向 backward + 与漂移同向）；
- **能力声明为 false 语义**：capabilities.posture=true 表示"管线已实现并运行"，不表示"能识别姿态"；补采清单见 posture_v01_diagnostics.md §6。

### P1（功能性限制）

- warmup 行为：v1.2 下启动 1–2s 即被漂移钉死（伪 warmup）；R2 落地后需重新定义真 warmup 语义；
- 无效帧路径（presence=-1）在当前 12 组中未出现（quality 全 ok）——该路径仅合成数据验证过；
- pilot/short 组缺 capture_start（meta=null）与时间窗 → 无法回放对齐；
- bin-距离未标定：region/main_bin 为几何先验，跨场景迁移需重标。

### P2（协作项）

- 秒表-雷达时钟偏移未实测（B 在办）→ D 正式评价须 `--allow-provisional --offset-s`；
- capabilities 字段格式与 A 对齐未完成；
- 标注缺口见 metadata_gaps.md（P03 无标注、P01 无姿态类别、chair_shift 移椅参数缺失等）。

## 5. 批量预测索引（12/12 组，2026-11-19）

| 目录 | 内容 |
|---|---|
| `outputs/t01-batch/<组>/m1_result_v12/` + `handoff/` | 8 组（P02/P03/P04 formal、P02 retest、chair_shift、pilot、short×2） |
| `outputs/t01-round2/<组>/m1_result_v12/` + `handoff/` | 3 组（neighbor、aisle、P04 retest） |
| `outputs/t01-p01/m1_result_v12/` + `handoff/` | P01 |
| `outputs/posture-v01/<组>/m1m2_result_v12_01/` + `handoff/` | 12 组 M1+M2（含姿态列行为） |

每组三件套：`<rid>_result.csv`（9 列接口文件，timestamp repr(float) 逐位保真）+ `<rid>_algorithm.json`（参数/能力/notes 全记录）+ `<rid>_meta.json`。诊断脚本在 `.deepworks/tmp/`（转换/批量/诊断，非交付物）。

## 6. 复现链路（组员要求①：从 raw.npz+meta.json 生成结果）

```powershell
# 单组（M1 v1.2）
python detect.py --recording examples/real/20261112_T01_P01_R01_c1r0_1dfft_raw.npz --config config/M1_dev112p_v0.2.json --emit-scores

# 单组（M1 v1.2 + M2 姿态 v0.1）
python detect.py --recording examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --posture-config config/M2_posture_v0.1.json --emit-scores

# 批量（目录输入自动遍历 *_raw.npz；写盘后自动 selfcheck，FAIL 非零退出）
python detect.py --recording examples/real --config config/M1_dev112p_v0.2.json --emit-scores

# 在线回放回归（vs 离线无姿态分支输出，逐字节比对）
python -X utf8 tools/live_seat.py --replay examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --out <dir> --emit-scores --expect <离线输出目录>

# 生成 A --live 启动文件（contract v1 新格式，capabilities 纯 bool）
python -X utf8 tools/live_seat.py --replay <npz> --config config/M1_dev112p_v0.2.json --out <dir> --emit-algorithm

# 在线逐帧转发 A（串行 POST；A 契约见 A README_实时入口）
python -X utf8 tools/live_seat.py --replay <npz> --config config/M1_dev112p_v0.2.json --post-url http://127.0.0.1:8766/api/frame --token <RADAR_A_FRAME_TOKEN> [--retry-max 8 --retry-base 1.0]
```

输入为 dsraw 归档时先转换：`python tools/dev112p_adapter.py <dsraw目录>`（chip1/rx0、region [1,3] main_bin 2）。

## 7. 关联文档

- 开发集验证报告：`outputs/t01-devset-validation/devset_validation_v12_m2.md`
- round-2 缺陷分析：`outputs/t01-round2/v1.2_round2_defect_analysis.md`
- 姿态诊断（三路线否决证据 + 补采清单）：`outputs/posture-v01/posture_v01_diagnostics.md`
- 元信息缺项：`outputs/t01-devset-validation/metadata_gaps.md`
- 在线 v0.1 验证报告（含 2026-11-19 e2e 补录）：`outputs/online-v01/online_v01_验证报告.md`
- B↔C 帧级接口提案（在线 v0.1，待 B 确认）：镜像 `对接事宜/2026-11-19_B-C帧级接口提案.md`
- A↔C capabilities 语义与在线异常处理（2026-11-19 收口，待 A 复核）：镜像 `对接事宜/2026-11-19_A-C_capabilities语义与在线异常处理.md`
- A 实时入口契约：归档 `A_stone140_应用与业务/README_实时入口.md`
- 接口规范 v1：归档 `C_cy071210_识别算法/`（posture/calibration_status 词表以 `algos/selfcheck.py` 为权威实现）
