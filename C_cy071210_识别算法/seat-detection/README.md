# seat-detection（自习室座位占用识别，离线算法 v0.1）

> 模块 C（cy071210）｜阶段二：无硬件算法实现与合成自测 ｜ 2026-11-17
> 设计文档：`docs/seat-detection-design-v0.1.md`（§编号常以 `D-` 引用）；接口规范 v1 见归档 `C_cy071210_识别算法/`

## 结构

```
seat-detection/
├── detect.py                  # 统一 CLI：读 recording → 算法 → result.csv + algorithm.json
├── algos/
│   ├── m1_features.py         # M1 v1.1 主检测（背景 EMA + 4 特征 + 滞回）
│   ├── b0_pointcloud.py       # B0 v0.1 候选③（2D CA-CFAR + MTI + 点数滞回）
│   ├── region.py              # 安装区域解析（三级降级 + warnings）
│   └── selfcheck.py           # result.csv C1-C8 校验（detect.py 写盘后自动跑）
├── config/
│   ├── M1_default.json        # 阈值 0.5/0.35、权重 0.45/0.30/0.25、EMA α=0.2、窗口 4 s
│   ├── B0_default.json        # CFAR p_fa=1e-3、guard(1,2)/train(2,4)、MTI ±1、点数滞回 2/3
│   └── region_params_v0.1.json# 安装尺寸（名义值，待现场标定）
├── tools/
│   ├── make_synthetic_g1.py   # G1 合成数据生成器（5 场景 × 2 段 × 2 层级）
│   ├── g1_analysis.py         # G1 分析：滑窗/阈值扫描/书包检验/B0 检验 → outputs/g1-preview/
│   └── annotation_spec_v0.1.md# 标注提案（给 D，待确认冻结）
└── examples/recordings/       # 生成输出（*_raw.npz / _meta.json / _scenario_script.json）
```

## 依赖

Python 3.12 + numpy + matplotlib（无 scipy/pandas）。Windows PowerShell 下直接用英文参数运行；
涉及中文文件名时用通配符路径，不要把中文路径作为 shell 参数传。

## 快速开始

```powershell
# 1) 生成 G1 合成数据（10 个 recording，60 s @ 1 Hz，5 场景）
python tools/make_synthetic_g1.py

# 2) 全部检测（1dfft→M1、2dfft→B0 自动路由；写盘后自动 selfcheck）
python detect.py --recording examples/recordings --emit-scores

# 3) G1 分析（报告 + 3 张图）
python tools/g1_analysis.py
```

单文件用法与可选参数：

```
python detect.py --recording examples/recordings/<rid>_raw.npz
python detect.py --recording <npz|目录> --algorithm M1 --config config/M1_default.json
python detect.py --recording <npz|目录> --region-params config/region_params_v0.1.json
python detect.py --recording <npz|目录> --strict        # input validation 失败即退出非零
```

输出：`<rid>_result.csv`（9 列接口文件）、`<rid>_algorithm.json`、
`<rid>_scores.csv`（--emit-scores 时的调试辅助文件，非接口文件）。

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

## G1 自测流程（§8、§11）

```powershell
python tools/make_synthetic_g1.py
python detect.py --recording examples/recordings --emit-scores
python tools/g1_analysis.py    # 报告：outputs/g1-preview/g1_report.md
```

验收（2026-11-17 自测结果）：selfcheck 20/20 PASS；**G1-a 通过**（静坐 FN 8.3% = 入座边界
confirm 延迟、空位 FP 0%）；**G1-b 通过**（残差判据 −0.3 dB）；bag 1d FP 0%、passby 1d FP 0 窗；
B0 检验 10/10 符合（RQ2 证据）。G1-c 待真实数据（B 端 30 min 采集）。详细判据与阈值扫描见报告。

## 已知限制与风险（§4.6、§12）

- **dsraw 不支持**：待 DS10012RN 原始协议（规范：不得猜测未知格式）；detect.py 报错退出；
- **书包放置瞬态**（合成峰值 0.74 > T_ON，3-4 帧全特征假阳性）：不靠分数余量区分——
  confirm=5 帧防抖拦截 + 背景 EMA 吸收 + 静态门兜底；真实数据瞬态形态需 G1-c 复核；
- **退化模式静态物锁死**：degraded 输入下静态强反射物会冻结背景造成持续误报
  （confirm 只延迟数帧，静态门依赖相位特征、退化下禁用）；G1 的 M1 指标只取 1dfft 复数路径；
- **B0 FA 闪现**：p_fa=1e-3 设计率下偶发 2-3 帧点聚簇触发短 presence 闪现——基线特性，
  2dfft-only 硬件上空位窗 FP ~8-10%；静态占用判定不依赖 B0 presence；
- B0 候选③作为基线需知会教师（课程只要求点云复现基线）；
- 安装区域用名义值，现场需标定（region_params_v0.1.json）。

## 归档约定

每阶段交付物同步至仓库 `C_cy071210_识别算法/`（保持只增不动他人目录）；本目录代码提交信息
用英文（repo 约定），报告文件名可中文。
