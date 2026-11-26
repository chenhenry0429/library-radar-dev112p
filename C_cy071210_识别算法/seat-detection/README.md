# seat-detection（自习室座位占用识别，离线算法 + 在线入口 v0.1）

> 模块 C（cy071210）｜当前阶段：真实数据回放与验证（T01 开发集 12 组）；在线逐帧入口 v0.1 已交付 ｜更新 2026-11-19
> 设计文档：`docs/seat-detection-design-v0.1.md`（§编号常以 `D-` 引用）；接口规范 v1 见归档 `C_cy071210_识别算法/`
> **能力边界先读**：`CAPABILITIES_AND_KNOWN_ISSUES.md`（v1.2 presence 有已知钉死缺陷；姿态 v0.1 为 gated 拒识——如实声明，不夸大）

## 结构

```
seat-detection/
├── detect.py                  # 统一 CLI：读 recording → 算法 → result.csv + algorithm.json（自动 selfcheck）
├── algos/
│   ├── m1_features.py         # M1 主检测（背景 EMA + 4 特征 + 滞回 + confirm + 静态门；离线/在线共用逐帧状态机）
│   ├── online_detector.py     # 在线入口 v0.1：SeatDetector（逐帧会话）+ project_dev112p_frame（B 单帧投影）
│   ├── m2_posture.py          # M2 姿态 v0.1（参考标定 + 特征 + 对比度质量门 + 拒识；仅离线）
│   ├── b0_pointcloud.py       # B0 v0.1 候选③（2D CA-CFAR + MTI + 点数滞回；仅离线）
│   ├── region.py              # 安装区域解析（三级降级 + warnings）
│   └── selfcheck.py           # result.csv C1-C8 校验（接口词表权威实现）
├── config/
│   ├── M1_default.json        # 合成域默认（阈值 0.5/0.35、权重 0.45/0.30/0.25、EMA α=0.2）
│   ├── M1_dev112p_v0.2.json   # T01 真实数据冻结快照（v1.2；含 algorithm_version 标识）
│   ├── M2_posture_v0.1.json   # 姿态 gated baseline 参数
│   ├── B0_default.json        # CFAR p_fa=1e-3、guard(1,2)/train(2,4)、MTI ±1、点数滞回 2/3
│   └── region_params_v0.1.json# 安装尺寸（名义值，待现场标定）
├── tools/
│   ├── dev112p_adapter.py     # dsraw 归档 → *_raw.npz + _meta.json（chip1/rx0，region [1,3]）
│   ├── live_seat.py           # 在线驱动：--replay 回放回归 / --post-url 逐帧转发 A（串行）
│   ├── make_synthetic_g1.py   # G1 合成数据生成器
│   ├── g1_analysis.py         # G1 分析 → outputs/g1-preview/
│   └── annotation_spec_v0.1.md# 标注提案（给 D）
├── examples/recordings/       # 合成数据
├── examples/real/             # 真实数据（12 组 T01：npz + meta，1dfft/2dfft 双层级）
└── CAPABILITIES_AND_KNOWN_ISSUES.md  # 能力与已知问题（重要）
```

## 依赖

Python 3.12 + numpy + matplotlib（无 scipy/pandas）。Windows PowerShell 下直接用英文参数运行；
涉及中文文件名时用通配符路径，不要把中文路径作为 shell 参数传。

## 快速开始（真实数据，T01 开发集）

```powershell
# 0)（如需）dsraw 归档 → npz+meta
python tools/dev112p_adapter.py <dsraw目录>

# 1) M1 v1.2 单组（写盘后自动 selfcheck，FAIL 非零退出）
python detect.py --recording examples/real/20261112_T01_P01_R01_c1r0_1dfft_raw.npz --config config/M1_dev112p_v0.2.json --emit-scores

# 2) M1 v1.2 + M2 姿态 v0.1（gated：门失败时 posture=unknown/calibration_status=invalid，原因在 notes）
python detect.py --recording examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --posture-config config/M2_posture_v0.1.json --emit-scores

# 3) 批量（目录自动遍历 *_raw.npz）
python detect.py --recording examples/real --config config/M1_dev112p_v0.2.json --emit-scores
```

输出：`<rid>_result.csv`（9 列接口文件，timestamp repr(float) 逐位保真）、`<rid>_algorithm.json`（参数/能力/notes）、
`<rid>_scores.csv`（--emit-scores 调试辅助，非接口文件）。

合成数据链路（G1 自测）：

```powershell
python tools/make_synthetic_g1.py
python detect.py --recording examples/recordings --emit-scores
python tools/g1_analysis.py    # 报告：outputs/g1-preview/g1_report.md
```

其他参数：`--algorithm M1|B0`（缺省按 meta.data_level 路由）、`--region-params <json>`、`--strict`。

## v1.2.1（离座判空修复，2026-11-21）

v1.2 在真实数据上「开场误报 + 离座不判空」，v1.2.1 用独立分支/独立配置修复，
v1.2 的配置与既有输出保持不变。

```powershell
# 0) 归档 → 奇偶分链（v1.2.1 输入格式 [frame, chirp_parity, range]）
python tools/dev112p_adapter.py <dsraw目录> --parity split --skip-2dfft --out examples/real_v121

# 1) v1.2.1 单组
python detect.py --recording examples/real_v121/<rid>_1dfft_parity_raw.npz --config config/M1_dev112p_v1.2.1.json --emit-scores

# 2) 批量 + 验收对照表（v1.2 vs v1.2.1：开场误报/静坐保持/走远判空/全空组）
python detect.py --recording examples/real_v121 --config config/M1_dev112p_v1.2.1.json --emit-scores
python tools/eval_v121.py      # → outputs/v1.2.1/v1.2.1_comparison.{md,csv}
```

要点（详见 `../outputs/v1.2.1/v1.2.1_fix_report.md`）：

- **R1 奇偶 chirp 分开**：避免 11× 奇偶幅度差 + 部分帧配比偏置污染背景；
- **R2 开场标定**：先积累 ≥10 s 有效帧（≥40 帧）再启用占用判断，标定期 presence=0；
  标定结束后再等满一个 10 s 微动窗才判决（避免摆位过渡段误入场）；
- **R3 判空**：F1 改用「泄漏归一残差」（bin/|bin0|），Bin1 微动指数（10 s 去趋势
  幅度 std/mean，按标定空座噪声底归一）连续 5 s 回到空座带 → 判空并重建快照；
  F1 稳定性（|Δf1| ≤ 6 dB）在释放时刻作否决；**判空快通道**：慢台阶/慢漂移把 m 抬起、
  但 Bin1 瞬时变化与背景都没动（`m_inst` 低、`f1n` 低）时也按空座计，避免释放被
  慢台阶推迟（849 帧现场 22.2 s→12.1 s）。
- **conf 语义**：`presence_confidence` 是对**当前判决**的置信度（判空时 m 越低越高、
  判人时 m 越高越高，floor 0.60），保证 A 的 0.60 门槛不会把「C 已判空」的帧丢掉
  （第二轮现场第 848 帧旧口径 0.525 曾打断 3 s 连续判空）。
- 结果：开场误报 100%→3%、邻座/过道全空 100%→0%、走远判空 0/8→4/8
  （其余为人未走出波束/静坐过静，见报告 §5）；484 帧现场抓包 0→1 于 27.48 s、
  1→0 于 67.53 s（11.61 s 误判已消除）。

```powershell
# 3) 在线逐帧回放一致性（v1.2.1 同样逐字节一致）
python tools/live_seat.py --replay examples/real_v121/<rid>_1dfft_parity_raw.npz \
    --config config/M1_dev112p_v1.2.1.json --out <live_out> --emit-scores --expect <离线输出目录>
```

## 在线入口（v0.1，2026-11-19）

`algos/online_detector.py`：`run_m1` 重构出的逐帧状态机（`make_m1_state`/`_m1_step`，离线/在线共用同一代码路径）之上的连续会话入口。

- **`SeatDetector(meta, m1_config, region_params, frame_period_s, ...)`**：一次会话一个实例；
  `feed_frame(剖面, ts, frame_id)` 返回九字段行 dict（contract v1）；`feed_dev112p(B单帧立方, 掩码, ts, frame_id)`
  由 C 侧投影（`project_dev112p_frame`，与离线适配器 `chirp_mean` 字节等价）后进入识别。
- **语义**：frame_id 0 起严格连续（跳号=会话错乱即报错）；timestamp 严格递增；
  零有效 chirp 帧输出 `invalid_frame`（presence=-1）且不推进识别状态机（防 NaN 毒化相位链）。
- **能力**：M1 实时占用 + 姿态占位（= 离线不带 `--posture-config`）；仅 1dfft。
  M2/B0 在线化未排期（回填/点云语义待定）。
- **验证**：12 组 T01 逐帧喂入 vs 离线 detect.py **逐字节一致**（result.csv + scores.csv）；
  投影 vs 离线适配器字节等价（含缺 chirp、int16 极值、单 chirp、交叉选路、守卫）；
  **C↔A 端到端预演（真 live_monitor.py --live）20/20 通过**：全帧 POST / 断流恢复
  （feed_interrupted→feed_resumed）/ 401 快败 / 400 快败且会话存活。
- **POST 失败规则**（与 A 收口，2026-11-19）：HTTP 状态错误不重试即中止（400 修当前帧、
  401 换 token）；网络错误指数退避重试（`--retry-max`/`--retry-base`），重试前 `GET /api/state`
  对齐防重复帧；耗尽即中止（不静默丢帧）。响应 `accepted_frame_id` 与帧号不符即报错。
- **A --live 启动文件**：`--emit-algorithm` 生成 `<rid>_algorithm_online.json`
  （contract v1 新格式、capabilities 纯 bool；detect.py 离线 algorithm.json 为旧回放格式，
  A --live 严格校验不收）；每次运行幂等重写，A 端启动用它。

```powershell
# 生成 A --live 启动所需的 algorithm.json（contract v1 新格式）
python -X utf8 tools/live_seat.py --replay examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --out <dir> --emit-algorithm

# 回放回归（vs 离线无姿态分支输出，逐字节比对）
python -X utf8 tools/live_seat.py --replay examples/real/<rid>_raw.npz --config config/M1_dev112p_v0.2.json --out <dir> --emit-scores --expect <离线输出目录>

# 逐帧转发 A（串行 POST，等响应再发下一帧；A 契约）
python -X utf8 tools/live_seat.py --replay <npz> --config config/M1_dev112p_v0.2.json --post-url http://127.0.0.1:8766/api/frame --token <RADAR_A_FRAME_TOKEN> [--interval 0.1935] [--retry-max 8 --retry-base 1.0]
```

B↔C 帧级契约（DevFrame 结构、丢帧的零掩码表达、握手 meta）：见镜像 `对接事宜/2026-11-19_B-C帧级接口提案.md`（提案，待 B 确认）。
A↔C capabilities 语义与在线异常处理（丢帧/断流重连/会话重置/POST 失败）：见镜像 `对接事宜/2026-11-19_A-C_capabilities语义与在线异常处理.md`（成文，待 A 复核）。

## 算法（设计文档 §4–§5）

- **M1 v1.1（主检测，1dfft 复数）**：timestamp 窗口（4 s，快照边界，不 resample）→
  背景去噪 EMA α=0.2（仅 presence=0 帧更新）→ 4 特征：F1 区域残差能量 / F2 主 bin 相位 std /
  F3 主 bin 呼吸频段能量占比 [0.1,0.5] Hz / F4 帧间能量跳变（预留，权重 0）→
  加权分数 → 滞回 T_ON=0.5 / T_OFF=0.35，**confirm=5 帧防抖 + 静态门 f23_floor=0.10**
  （有人态下 4 s 窗 (F2n+F3n) 均值 <0.10 强制释放——防"人走包留"锁死）。
  quality：`invalid_frame`、`low_signal`（区域平均功率 < 10^(−60/10)）→ presence=-1。
- **B0 v0.1 候选③（2dfft，RQ2 证据）**：2D CA-CFAR（向量化积分图，边界按实际训练单元数重算 α）→
  3×3 局部极大 → MTI（doppler DC ±1 滤除）→ 区域过滤 → 点数滞回（2 帧进/3 帧出，翻转清零对侧游程）。
  静态占用被 MTI 滤除，静坐时无持续检出；p_fa=1e-3 → 每段 ~7 个 FA 点帧，
  偶发聚簇的短 presence 闪现为基线特性（静态占用判定不依赖 B0 presence）；与 M1 互补。
- 实数/退化输入（如 M1 误收 2dfft 合并）走 degraded 路径并标注 `degraded_input=true`。
- **M2 v0.1（姿态，gated baseline，1dfft）**：presence=1 稳定 10s 后取 50 帧参考窗 → 每帧
  region±2 bins 能量重心/能量和 → **对比度质量门**（参考窗 vs 紧随窗重心差 < 2.0×合并 std →
  拒识：posture=unknown、calibration_status=invalid，量化原因写入 notes）→ 过门后
  Δcentroid ±0.02 bin 滞回分类（reference/forward/backward）+ 能量比守卫。
  当前 T01 数据 11/12 组被门拒（泄漏 >> 人体信号，见 posture_v01_diagnostics.md）。

## 验证状态（分层如实声明）

- **合成域（G1）**：selfcheck 20/20 PASS；G1-a/b 通过（静坐 FN 仅 confirm 边界延迟、空位 FP 0%、
  残差判据 −0.3 dB）；bag/passby FP 0；B0 检验 10/10。详见 outputs/g1-preview/g1_report.md。
- **真实域（T01 开发集 12 组，v1.2）**：契约校验 12/12 PASS，但 **presence 存在钉死缺陷**——
  11/12 组空场误报 93.8–98.7%、离座零释放 9/9；P01 唯一通过属三重幸存者偏差。
  根因/证据/修复方向见 `outputs/t01-round2/v1.2_round2_defect_analysis.md`；
  全量指标见 `outputs/t01-devset-validation/devset_validation_v12_m2.md`；
  能力边界汇总见 `CAPABILITIES_AND_KNOWN_ISSUES.md`。

## G1 自测流程（§8、§11）

```powershell
python tools/make_synthetic_g1.py
python detect.py --recording examples/recordings --emit-scores
python tools/g1_analysis.py    # 报告：outputs/g1-preview/g1_report.md
```

验收（2026-11-17 自测结果）：selfcheck 20/20 PASS；**G1-a 通过**（静坐 FN 8.3% = 入座边界
confirm 延迟、空位 FP 0%）；**G1-b 通过**（残差判据 −0.3 dB）；bag 1d FP 0%、passby 1d FP 0 窗；
B0 检验 10/10 符合（RQ2 证据）。G1-c 待真实数据（B 端 30 min 采集）。详细判据与阈值扫描见报告。

## 已知限制与风险（§4.6、§12；真实数据层见 CAPABILITIES_AND_KNOWN_ISSUES.md）

- **v1.2 presence 钉死（真实数据，最高优先）**：绝对阈值 × 热状态基数 2.9× × 漂移 −1.9~−6.2%/min
  → 启动 1–2s 钉死 1；修复方向 R1–R5 属团队决策，配置冻结。合成域 G1 全过 ≠ 真实域可用；
- **姿态类别当前不可分**：泄漏高人体信号 1–2 个数量级，三条特征路线量化否决；M2 v0.1 = 拒识管线；
- **dsraw 不支持**：待 DS10012RN 原始协议（规范：不得猜测未知格式）；detect.py 报错退出；
- **书包放置瞬态**（合成峰值 0.74 > T_ON，3-4 帧全特征假阳性）：不靠分数余量区分——
  confirm=5 帧防抖拦截 + 背景 EMA 吸收 + 静态门兜底；真实数据瞬态形态需 G1-c 复核；
- **退化模式静态物锁死**：degraded 输入下静态强反射物会冻结背景造成持续误报
  （confirm 只延迟数帧，静态门依赖相位特征、退化下禁用）；G1 的 M1 指标只取 1dfft 复数路径；
- **B0 FA 闪现**：p_fa=1e-3 设计率下偶发 2-3 帧点聚簇触发短 presence 闪现——基线特性，
  2dfft-only 硬件上空位窗 FP ~8-10%；静态占用判定不依赖 B0 presence；
- B0 候选③作为基线需知会教师（课程只要求点云复现基线）；
- 安装区域用名义值，现场需标定（region_params_v0.1.json）；bin-距离映射未标定。

## 归档约定

每阶段交付物同步至仓库 `C_cy071210_识别算法/`（保持只增不动他人目录）；本目录代码提交信息
用英文（repo 约定），报告文件名可中文。

现场原始数据和配套的真实样例、复验输出保存在原 [C PR #9](https://github.com/chenhenry0429/library-radar-dev112p/pull/9)；在 `main` 运行真实目录示例前，先准备组内样本。合成自测不依赖这些文件。
