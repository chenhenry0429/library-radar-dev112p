# T01/P01/R01 真实数据首次回放校验报告（v0.1）

> 日期：2026-11-17 ｜ 执行：C（DeepWorks 离线管线） ｜ 范围：DEV112P 首组真实录制 `20261112_T01_P01_R01`
> 结论先行：**管道端到端在真实硬件上全部跑通**（归档校验 → 规范适配 → M1/B0 回放 → 自检通过）；
> 但 M1 v1.1 在本段数据上存在**结构性判别困境**（详见 §4），当前有人/无人结论以数据驱动基线
> （[baseline_presence_result.csv](baseline_presence_result.csv)）为准，**算法结果未冒充真值**。

## 1. 归档校验（PASS）

- SHA256 与录制方 meta 完全匹配；raw.npz 5 键结构完整；int16 形状 (840, 2, 2, 64, 8, 2)。
- 帧周期 0.19346 s → 5.168 Hz；840 帧时间戳连续（C1 约束满足）。
- 部分帧 2 个（帧 0 缺 56 chirp、帧 839 缺 164 chirp），无全零帧。
- **chip0/bin0 负向饱和 3.1%** → 全程选用 chip1/rx0（呼吸证据最干净、无饱和）。
- `range_bin_distance_m: null`：几何未标定。

## 2. 规范适配（dev112p_adapter.py）

| 产物 | 形状 | 说明 |
|---|---|---|
| `examples/real/..._1dfft_raw.npz` + meta | (840,8) complex64 | M1 输入；valid-chirp 复均值 |
| `examples/real/..._2dfft_raw.npz` + meta | (840,64,8) complex64 | B0 输入；chirp 轴 64 点 FFT，DC 在 doppler bin 0（无 fftshift），缺包 chirp 填零 |

meta 19 必填字段完整；`installation.region_bins=[1,3]`、`main_bin=2` 为**试点信号标定**
（呼吸/功率证据，非几何标定），已在 meta 注释中诚实记录。部分帧 0/839 的 2dfft 有 sinc 伪影。

## 3. 信号分析结论（check/check2/check3）

- 功率 dB（chip1/rx0）：bin0=84.8、bin1=76.3（静态杂波）、**bin2=49.1（胸口反射）**、bin3=37.0。
- 呼吸证据：bin2 呼吸带 0.21 Hz 占比 0.46；**但空座段同单元也有 0.59@0.33 Hz**——环境低频扰动
  与呼吸同带，这是后续 F2/F3 失效的根源。
- 残差时间线：对静态背景存在 **+8.8 dB/162s 单调漂移**（静态参考背景不可用，EMA 设计被证实必要）。
- **口令剧本严重偏离实测**（probeB 变化点簇，阈值 33.1 dB）：实际入座 ≈19.7–29.2s（剧本 15s）；
  剧本 125–140s 离座**无任何变化证据**（人未离开）；尾部 156.3–162.3s 有大幅动作，性质未知。

## 4. M1 v1.1 标定与回放（三轮迭代，结构性困境已定位）

探针：A（冻结背景语义）/ B（连续 EMA 变化点）。三轮生产回放：

| 轮次 | 配置要点 | 结果 | 根因 |
|---|---|---|---|
| 1 | confirm=22, 锚[37.5,41.5], α=0.2 | 全程 presence=0 | EMA 以 τ≈1s 吸收入座者，4.3s confirm 窗口内残差塌回 |
| 2 | confirm=6, 锚[30,38], α=0.2 | 72s 才触发 | 静止坐姿被背景吸收，仅姿势动作期间越阈 |
| 3 | confirm=6, 锚[30,38], α=0.02 | 4.06s 起全程 presence=1 | 空座环境残差 vs 早期背景 37 dB 与入座提升 40.5 dB 仅差 3.3 dB，锚点无法分离 |

**结论**：困境是结构性的——本设备上空座环境扰动（vs 慢背景）≈ 有人提升量级，EMA 背景机制
在"吸收入座者"与"空座误触发"之间无可用工作点。配置级调参已用尽，修复需要设计层改动
（候选：入座前背景快照对比、静态杂波相位漂移补偿、多芯片冗余），列为后续工作。
附带发现：静态门在本数据上失效（空座 f23≈1.3 恒高于 floor 0.10，环境扰动所致）；
F2/F3 判别力弱（空座相位 std 0.024 > 有人 0.012 rad，反转）。
配置存档：`seat-detection/config/M1_dev112p_v0.1.json`（含完整 rationale）。

## 5. 数据驱动基线（当前有人/无人结论的唯一权威）

> **v0.2 修订（A 端反馈）**：handoff 三件套规范化——`capabilities` 改为列表 `["presence"]`；
> 新增顶层 `window_s=0.1935` / `smoothing="none"`；`config` 结构化；无人帧 posture 由
> `unknown` 改为 `not_applicable`（102 帧，有人帧仍 `unknown`）；`time_base` 注明时间戳由
> 名义帧周期推算、未墙钟对齐（待与 B 端采集陈植轩确认）。版本固定
> **data-driven-baseline v0.2**；用途界定：单次录制占用基线联调，不作 M1/坐姿验收。

[baseline_presence_result.csv](baseline_presence_result.csv)：统一接口 9 列、840 帧连续；
0–19.737s 无人（102 帧）、19.737–162.47s 有人（738 帧）；posture：有人帧 `unknown`、
无人帧 `not_applicable`（v0.2；剧本不可信、M1 不输出姿态）；尾部 156.3s 起置信度降至
0.70（动作性质未知，保守判有人）。
证据链：§3 功率/呼吸/变化簇。**待 D 独立标注确认后方可升级为评价真值。**

图（[figures/](figures/)，视觉工具不可用，请人工复核）：
- [signal_raw.png](figures/signal_raw.png) — 8 bin 功率 + bin2 相位时间线，分段色带
- [timeline_judgment.png](figures/timeline_judgment.png) — M1 特征/得分 vs 基线对比
- [rd_snapshots.png](figures/rd_snapshots.png) — 距离-多普勒快照（空座/有人/前倾）
- [b0_timeline.png](figures/b0_timeline.png) — B0 判定与点数 vs 基线

## 6. B0 官方基线回放（PASS，RQ2 证据）

- coverage 22.1%；空座段 FA 率 8.8%，有人段检出率 24%。
- **运动簇与 probeB 变化簇高度吻合**：18.0–26.5s（入座过程，比落座簇早 1.7s，为走近座位的
  行走运动）、71.8/78.4/83.6/88.0/102.75s（姿势过渡）——B0 在真实数据上表现为"运动检测器"。
- 静态占用弱检出为**设计特性**（MTI 滤除 doppler DC），与合成 G1 的 RQ2 结论一致；
  M1（静态占用）与 B0（动态事件）互补的架构判断在真实数据上成立。

## 7. 标注骨架

[labels_draft_unverified.csv](labels_draft_unverified.csv)：14 段、左闭右开、`seat_id=T01`、
annotator=`C_draft_unverified`（C 起草、未验证，不冒充 D）。边界为数据驱动；posture 全部
`unknown`；posture_shift 事件行对应 probeB 簇；**剧本的 125–140s 离座未采信**（无证据）。
待 D 按标注规范（annotation_spec_v0.1）独立标注后，以 D 的 labels.csv 为准。

## 8. 已知限制与后续工作

| 项 | 状态 | 后续 |
|---|---|---|
| M1 结构困境 | 已定位（§4） | 设计层修复（背景快照对比/漂移补偿），下轮迭代 |
| 几何标定 | region 为信号标定 | 现场测量 bin→米（DS10012RN 其余 null 同步处理） |
| 环境扰动 | 空座呼吸带存在 0.33 Hz 扰动 | 多日复测确认扰动源；静态门重设计 |
| D 独立标注 | 骨架已备 | 视频对齐（±0.5s 目标）→ D 标注 → g1_analysis measured 模式评价 |
| GitHub 仓库可见性 | 用户决策 | 未决（例外公开上传 vs 私有仓库） |
| 计划书 DEV113P→DEV112P | 用户确认 | 未决 |

## 9. 产物清单

- `seat-detection/examples/real/` — 规范适配数据（1dfft/2dfft npz + meta）
- `seat-detection/config/M1_dev112p_v0.1.json` — 真实设备配置（含 rationale）
- `seat-detection/algos/m1_features.py` — f3_denominator 开关（合成回归 20/20 PASS 无变化）
- `seat-detection/tools/dev112p_adapter.py` — 适配器（--region-bins/--main-bin）
- `outputs/t01-p01/baseline_presence_result.csv` — 数据基线（统一接口）
- `outputs/t01-p01/labels_draft_unverified.csv` — 标注骨架
- `outputs/t01-p01/figures/` — 4 张图
- `outputs/t01-p01/m1_result/`、`b0_result/` — M1/B0 回放原始输出（PASS C1–C8）
