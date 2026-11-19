# 能力与已知问题说明（CAPABILITIES_AND_KNOWN_ISSUES）

- 模块：C（cy071210）｜`seat-detection/` ｜ 更新：2026-11-19
- 一句话结论：**源码可运行、批量预测已完成 12/12 组、契约校验全过；但 M1 v1.2 在真实开发集上存在 presence 钉死缺陷（结构性，修复待团队决策），姿态 v0.1 以显式拒识交付（当前数据不可分）。** 本文档按组员要求如实声明能力边界，不夸大。

---

## 1. 能力总表

| 能力 | 状态 | 依据 |
|---|---|---|
| presence 管线（背景更新/特征提取/阈值/滞回，implemented as program） | ✅ 已实现为程序，从 `raw.npz + meta.json` 直接生成结果 | §2；`detect.py` + `algos/m1_features.py` |
| presence 在真实 T01 数据上的可用性 | ❌ **不可用（v1.2 钉死缺陷）**，11/12 组空场误报 93.8–98.7%、离座零释放 9/9 | §4.1；round-2 缺陷报告；开发集验证报告 |
| 姿态管线（参考标定/特征/质量门/拒识，implemented as program） | ✅ 已实现（M2 v0.1 gated baseline） | §3；`algos/m2_posture.py` |
| 姿态分类在当前数据上输出 | ❌ **11/12 组拒识（unknown/invalid）**；P01 过门但方向不可验证 | §4.2；posture_v01_diagnostics.md |
| 合成数据自测（G1） | ✅ 20/20 PASS（合成域） | README「G1 自测流程」 |
| 批量预测 + 契约校验 | ✅ 12/12 组 result.csv C1–C8 PASS | §5；`algos/selfcheck.py` |
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

## 3. M2 姿态管线 v0.1（gated baseline，对应组员要求④）

1. **参考标定**：presence=1 稳定 10s 后取 50 帧连续窗统计（能量重心/能量和）；
2. **特征提取**：每帧 region±2 bins（排 bin0 泄漏）的能量重心与能量和；
3. **对比度质量门**：参考窗 vs 紧随窗重心差 < 2.0×合并 std → **拒识**（posture=unknown、calibration_status=invalid，量化原因写入 algorithm.json notes）；
4. **分类（过门才启用）**：Δcentroid ≥ ±0.02 bin → forward/backward；能量比越界 [0.5,2.5] → unknown。

接口词表：posture ∈ {reference, forward, backward, unknown, not_applicable}；calibration_status ∈ {uncalibrated, calibrating, valid, invalid}。`--posture-config` 缺省不加载（presence 交付保持 v1.2 可比）。

## 4. 已知问题（按严重度）

### P0-1 presence 钉死（结构性缺陷，v1.2 不可用）

- 现象：11/12 组 first1 落在 t=1.16–2.13s（真实首入座 17–21.6s），空场误报 93.8–98.7%，9/9 离座事件零释放；P01 唯一"通过"（0% FP、入座确认 +4.45s provisional）；
- 根因（三因素叠加，量化见 round-2 报告 §2）：f1 绝对阈值在 P01 冷机基数 3538 上开环标定（[31.6,224] counts），热机基数 7000–10300 下放大 2–2.9×；录制内漂移 −1.9~−6.2%/min（快初始衰减 + 慢线性两级）打穿阈值；frame-0 奇偶配比偏置 +139（tx_polling=1 奇偶差 11×）污染种子帧（P01 恰好 25/25 零偏置=幸存）；
- P01"通过"是**三重幸存者偏差**（冷机低基数 + 零偏置种子 + 无长空场段触发失败路径）——不能作为能力证据；
- 静坐者真实签名在**方差层**（空座 std 0.1% vs 静坐 0.2–1.0%，差 2–8×），f3/f4 已看到人但权重合计 0.2；
- 修复方向 R1（adapter 奇偶平衡）+ R2（f1 尺度归一）+ R3（方差/动态提权）+ R5（多强度重标定）——**属团队决策，本任务配置冻结**；trim12 what-if 已证明单修种子不够。

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
```

输入为 dsraw 归档时先转换：`python tools/dev112p_adapter.py <dsraw目录>`（chip1/rx0、region [1,3] main_bin 2）。

## 7. 关联文档

- 开发集验证报告：`outputs/t01-devset-validation/devset_validation_v12_m2.md`
- round-2 缺陷分析：`outputs/t01-round2/v1.2_round2_defect_analysis.md`
- 姿态诊断（三路线否决证据 + 补采清单）：`outputs/posture-v01/posture_v01_diagnostics.md`
- 元信息缺项：`outputs/t01-devset-validation/metadata_gaps.md`
- 接口规范 v1：归档 `C_cy071210_识别算法/`（posture/calibration_status 词表以 `algos/selfcheck.py` 为权威实现）
