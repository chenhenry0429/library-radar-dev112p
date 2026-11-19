# seat-detection（自习室座位占用识别，离线算法）

> 模块 C（cy071210）｜当前阶段：真实数据回放与验证（T01 开发集 12 组）｜更新 2026-11-19
> 设计文档：`docs/seat-detection-design-v0.1.md`（§编号常以 `D-` 引用）；接口规范 v1 见归档 `C_cy071210_识别算法/`
> **能力边界先读**：`CAPABILITIES_AND_KNOWN_ISSUES.md`（v1.2 presence 有已知钉死缺陷；姿态 v0.1 为 gated 拒识——如实声明，不夸大）

## 结构

```
seat-detection/
├── detect.py                  # 统一 CLI：读 recording → 算法 → result.csv + algorithm.json（自动 selfcheck）
├── algos/
│   ├── m1_features.py         # M1 主检测（背景 EMA + 4 特征 + 滞回 + confirm + 静态门）
│   ├── m2_posture.py          # M2 姿态 v0.1（参考标定 + 特征 + 对比度质量门 + 拒识）
│   ├── b0_pointcloud.py       # B0 v0.1 候选③（2D CA-CFAR + MTI + 点数滞回）
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
