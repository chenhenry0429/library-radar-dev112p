# M2 姿态识别 v0.1 诊断报告（gated baseline 的证据链）

- 日期：2026-11-19
- 作者：C（算法实现），诊断脚本见 `.deepworks/tmp/`（posture_diag.py / doppler_diag.py / doppler_diff_diag.py / p01_posture_diag.py / p01_m2_align.py）
- 状态：**结论已定**——当前 T01 硬件数据上姿态类别不可分；v0.1 以「对比度质量门 + 显式拒识」交付，非缺省占位
- 关联交付：`seat-detection/algos/m2_posture.py`、`config/M2_posture_v0.1.json`、`detect.py --posture-config`；批量结果 `outputs/posture-v01/<组>/`

---

## 1. 问题设定

开发集里带姿态环节的录制与标注现状：

| 录制 | 姿态环节 | 标注质量 |
|---|---|---|
| t01-p02-formal（801 帧） | 61.17 前倾 / 92.50 回正 / 100.29 后靠 | 事件表齐全，**无类别幅度记录** |
| t01-p04-formal（807 帧） | 61.01 前倾 / 91.16 回正 / 98.09 后靠 | 同上 |
| t01-p02-retest / t01-p04-retest | 后靠 + 走/回 | 无前倾环节 |
| t01-p01 | 计划含前倾/后靠 | draft 仅 6 个 posture_shift 事件时刻（71.2/78.4/90.8/101.8/140.1 起），**无类别标签、未验证** |
| t01-p03-formal | 计划有 | **现场确认无任何实测标注** |
| 其余 6 组 | 无姿态环节（邻座/过道/空场/移椅/pilot/short） | — |

即：开发集只有 2 组（P02/P04 formal）有可信的姿态事件时刻，其中前倾片段仅出现在这 2 组。标注无方向幅度、无保持时长约束。

## 2. 三条特征路线的量化否决

### 2.1 路线一：幅度谱特征（能量重心 centroid / 能量和 sumP）

**posture_diag.py，P02/P04 formal + 2 组 retest，分段统计（bins1–5，region[1,3]±2）：**

- 各段重心均值全部落在 **1.055–1.081 bin** 区间；类间差（前倾段 vs 回正段 vs 后靠段）**0.001–0.003 bin**，与段内 std（0.001–0.006 bin）同量级——类间差 ≤ 噪声；
- 峰位 bin 在所有段恒为 **bin1**（TX 泄漏主导），从未落到人体 bin2；
- sumP 类间差 < 15%，同时段内波动 ±10% 量级。

**p01_posture_diag.py（P01，唯一冷机录制）：**

- 重心从 1.1524 单调升至 1.1717（全程），ΔP 单调 −0.66%→−5.34%（即 −4.5%/min 设备漂移）——重心变化与漂移轨迹重合，不与 draft 事件对齐；
- bin1 泄漏电平 3536 vs 人体 bin2 208–315：**泄漏高一个数量级以上**，人体信号占比仅 5–8%。

**结论：幅度谱路线在当前数据上不可分。** 类间差 0.001–0.01 bin，泄漏底噪决定一切。

### 2.2 路线二：多普勒不对称（2dfft 原谱）

**doppler_diag.py，P02/P04 formal：**

- 前倾/后靠动作窗的多普勒不对称度 D/R 恒为 **−0.93 ~ −0.98**（理论上静止泄漏应为 0 附近镜像对称）——静态泄漏镜像主导整个多普勒轴；
- 前倾窗与后靠窗的 D/R 差 < 0.02，无方向信息。

**结论：2dfft 多普勒路线死。** 泄漏镜像电平淹没运动分量。

### 2.3 路线三：帧差多普勒（ΔX = X2[t] − X2[t−1]）

**doppler_diff_diag.py：**

- 帧差谱的 D/R 系统性负偏（**−0.08 ~ −0.62**），仍有泄漏残差主导；
- P02 formal 前倾动作窗 D/R = −0.35 vs 后靠动作窗 −0.52——**同号、无一致翻转**；retest 组同样。

**结论：帧差多普勒路线死。** 方向翻转不可检。

## 3. v0.1 的形态决定：gated baseline（拒识管线）

三条特征路线量化否决后，组员反馈指出的路线（B：「不足则给参考标定、特征提取、拒识逻辑 + 列出需要补充的片段」）就是唯一诚实选项。v0.1 = **完整管线 + 对比度质量门**：

1. **参考标定**：presence=1 稳定 10s 后取 50 帧连续窗的幅度谱统计（centroid/sumP）；
2. **特征提取**：每帧 region±2 bins（排除 bin0 泄漏）的能量重心与能量和；
3. **对比度质量门（v0.1 核心）**：参考窗 vs 紧随 50 帧窗的重心差 < 2.0 × 合并 std → 拒识，**posture 全程 unknown、calibration_status=invalid**（接口「参考无效」语义），量化原因写入 algorithm.json notes；
4. **分类（过门才启用）**：Δcentroid ≥ ±0.02 bin → forward/backward；sumP 比值越界 [0.5, 2.5] → unknown（参考失效守卫）。

门参数即诊断结论的可执行形式：真实姿态变化必须让重心差超过段内噪声 2 倍才值得分类——11 组热机录制全部未过门（拒识原因：`INSUFFICIENT_PERSON_CONTRAST: |delta_centroid|=0.001-0.005 bin vs pooled_std=0.001-0.003 bin, ratio<2.0`）。

## 4. 批量运行结果（12/12 契约 PASS）

| 组 | gate_pass | posture 分布（帧数） | 说明 |
|---|---|---|---|
| t01-p01 | **True** | not_applicable 125 / unknown 102 / reference 290 / backward 323 | 唯一冷机；见 §5 |
| t01-p02-formal | False | not_applicable 6 / unknown 795 | 拒识 |
| t01-p03-formal | False | not_applicable 6 / unknown 791 | 拒识 |
| t01-p04-formal | False | not_applicable 11 / unknown 796 | 拒识 |
| t01-neighbor | False | not_applicable 6 / unknown 317 | 拒识 |
| t01-aisle | False | not_applicable 6 / unknown 307 | 拒识 |
| t01-p02-retest | False | not_applicable 11 / unknown 486 | 拒识 |
| t01-p04-retest | False | not_applicable 7 / unknown 497 | 拒识 |
| t01-chair-shift | False | not_applicable 6 / unknown 571 | 拒识 |
| pilot-01 | False | not_applicable 6 / unknown 343 | 拒识 |
| short-01 | False | not_applicable 9 / unknown 104 | 拒识 |
| short-02 | False | not_applicable 8 / unknown 46 | 拒识（无 50 帧连续窗，参考未建立） |

拒识路径（门失败/参考窗不足）下 presence=1 帧全部 `posture=unknown + calibration_status=invalid`，presence=0 帧 `not_applicable`——契约 C2/C3/C6/C8 全程满足（selfcheck 12/12 PASS）。

## 5. P01 个案：门通过 ≠ 姿态识别有效

P01 是唯一冷机录制（泄漏底 3536 vs 热机 7000–10300，人体占比相对高），也是唯一过门组。但其分类输出**不能声称姿态识别有效**，理由如实列出：

1. **翻转时刻与 draft 事件相关**：4/5 个 posture_shift 事件窗口有 0.5–2.4s 级翻转响应（71.6 vs 71.2；79.7–84.9 簇 vs 78.4–84.2；103.3 vs 101.8；140.3–142.8 簇 vs 140.1–144.2）；第 5 个（90.8–91.7）落在既有 backward 段内无翻转；
2. **但方向不可验证**：draft 无方向标签，无法判断 backward 输出是否对应真实后靠；
3. **单向翻转与漂移同向**：全程只有 backward 段、无 forward 段，且重心轨迹单调上升与 −4.5%/min 漂移重合（§2.1）——不能排除「漂移伪迹恰好在事件时刻附近越过阈值」；
4. 坐稳静止期（draft 29.2–71.2 无事件）M2 输出稳定 reference（43.9–71.4），这一点与「漂移越阈值前不翻转」自洽。

**结论：P01 的输出按「疑似信号、方向未验证、受漂移污染」如实记录，仅作开发集排查素材，不作为能力证据。**

## 6. 补采清单（使姿态可分所需的片段）

按「特征族 + 标注」两个维度列缺项，优先级从高到低：

1. **带方向标注的姿态片段（最关键）**：每桌号 × {前倾到位、后靠到位、回正} 各 ≥3 次、每次保持 ≥30s，秒表记到位/结束双时刻；当前开发集仅 P02/P04 formal 各 1 次前倾，且无方向幅度记录；
2. **冷机/低泄漏条件录制**：开机 ≤2min 开始、连续 ≥10min，用于隔离漂移（P01 是唯一冷机组且无类别标注）；
3. **多桌号/多体型/多距离**：现有姿态环节只有 2 名受试者、1 个桌位；区域 [1,3] 的 bin±2 窗内至少 3 个体型/距离组合；
4. **空椅/加物对照**：同桌号无人的前后各 60s（隔离泄漏漂移 vs 人体静止），chair_shift 组移椅参数（方向/距离）需现场记录；
5. **秒表-雷达时钟对齐**：事件时刻当前全部依赖秒表近似（B 在办的偏移测量），姿态验证需要帧级（±0.5s）对齐。

## 7. 版本与配置

- `algorithm_version`: `M1 v1.2 + M2 v0.1 (gated)`（posture 启用时）
- 配置：`config/M2_posture_v0.1.json`（settle_s=10, ref_frames=50, next_frames=50, contrast_factor=2.0, centroid_threshold_bins=0.02, sump_ratio=[0.5,2.5], margin_bins=2）
- 接口词表：posture ∈ {reference, forward, backward, unknown, not_applicable}；calibration_status ∈ {uncalibrated, calibrating, valid, invalid}
