# 交付说明：第一组实测数据三件套（状态机 / 页面验收用）

> 录制：`20261112_T01_P01_R01`（chip1/rx0 链路，840 帧 @ 5.168 Hz，时长 162.5 s）
> 版本：**data-driven-baseline v0.2（已固定）**；v0.2 按 A 端反馈规范化字段
> 交付方式：本地产物目录（共享仓库地址与可见性未决，确定后可原样提交）

## 三件套（本目录）

| 文件 | 说明 |
|---|---|
| `20261112_T01_P01_R01_c1r0_1dfft_result.csv` | 统一接口 9 列、840 帧连续；**占用基线判定** |
| `20261112_T01_P01_R01_c1r0_1dfft_algorithm.json` | 能力声明（列表格式）、config/window_s/smoothing、时间来源、已知问题、字段语义 |
| `20261112_T01_P01_R01_c1r0_1dfft_meta.json` | 与数据同一份的 19 必填字段 meta（适配器产物） |

## 用途界定（A 端确认）

本文件用于**单次录制的占用基线联调**（状态机 + 页面），**不作为 M1 算法验收、也不作为坐姿算法验收**。

## 当前能力：只支持占用，不支持坐姿

- **占用（presence）**：支持。0–19.737s 无人（102 帧），19.737–162.4665s 有人（738 帧）。
  边界帧 102（t=19.737s）。
- **坐姿（posture）**：**不支持**。有人帧 `posture=unknown`（无法判断）；无人帧
  `posture=not_applicable`（不适用）。`posture_confidence` 为空。`capabilities` 为
  `["presence"]`（列表格式，与 demo 四件套一致）。

## 字段规范（v0.2，对齐 DEMO_ONLY/release_demo 格式）

- `capabilities`：列表（本版本仅 `["presence"]`）
- `window_s`：`0.1935`（逐帧粒度 = 名义帧周期；离线基线无聚合窗口）
- `smoothing`：`"none"`（离线基线，无流式平滑）
- `config`：判定参数结构化（边界、变化点阈值 33.1 dB、EMA α=0.2、region [1,3]/main_bin 2、
  置信度与 posture 规则）
- `time_base.source = "nominal_frame_period"`：**timestamp 由名义帧周期推算**
  （frame_id / 5.168 Hz，共 840 帧），未与采集端墙钟对齐；来源说明待与 B 端采集（陈植轩）确认。

## 重要：这份 result 的性质

它是**数据驱动基线**（离线信号证据构造：变化点簇 + 残差提升 + 呼吸带峰），不是算法流式输出。
**M1 v1.1 算法版在本录制不可用**：α=0.2 漏检静坐者（72s 才触发），α=0.02 全程误报（4.1s 起）。
结构性原因：空座环境残差（vs 慢背景 37 dB）与入座提升（40.5 dB）仅差 3.3 dB，EMA 背景无可用
工作点；修复需设计层改动（入座前背景快照 / 漂移补偿），在途。**状态机与页面联调以本基线
CSV 为准；M1 修复后替换为算法输出。**

## 已知问题（详见 algorithm.json `known_issues`，摘要）

1. M1 算法版全程误报/漏检（上文）；集成期间不要消费 `m1_result/` 的 presence。
2. 静态门失效（空座环境低频扰动占据呼吸带）；F2/F3 判别力反转。
3. 静态杂波相位漂移 +8.8 dB/162s → 长录制 leave 后可能不释放。
4. 几何未标定：region [1,3] / main_bin 2 为**本座位信号标定**，不可泛化到其他座位。
5. 尾部 156.3s 起置信度降至 0.70（大幅动作、性质未知、保守判有人）。
6. 部分帧 2 个（帧 0/839，quality 仍 ok）；chip0/bin0 饱和 3.1%（已选 chip1/rx0 规避）。
7. 单条录制、单座位、单链路：验收范围仅限本数据。
8. B0（`b0_result/`）为运动检测器（静态占用弱检出是设计特性）：不要把 B0 presence 接成
   静坐占用信号源。
9. 时间基为名义帧周期，未墙钟对齐（待与陈植轩确认）。

## 状态机对接提示

- presence 沿 19.737s 单边沿；`presence_confidence` 为证据强度（0.90/0.70），非算法概率。
- `target_count`：presence=1 → 1（单座假设）。
- M1 修复后算法将有 ~4s 确认时延（confirm 窗口），状态机滞回请按此预留。

## 关联产物

- 校验报告：`outputs/t01-p01/verification_report.md`
- 标注骨架：`outputs/t01-p01/labels_draft_unverified.csv`（14 段，待 D 确认）
- 图：`outputs/t01-p01/figures/`（4 张）
- M1/B0 原始回放：`outputs/t01-p01/m1_result/`、`b0_result/`（算法侧参考）
