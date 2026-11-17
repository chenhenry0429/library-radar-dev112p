# 标注格式提案 v0.1（给 D）

> 作者：C（cy071210）｜日期：2026-11-17｜状态：**提案，待 D 确认后冻结**
> 依据：离线接口规范 v1 §7（labels.csv）+ seat-detection 设计文档 §9
> 原则：算法结果不能冒充真值；labels.csv 仅用于评价，不进入算法推理与 A 状态机

## 1. labels.csv 列定义

沿用规范 §7 建议列，UTF-8、首行表头、区间统一左闭右开 `[start, end)`：

```
start_timestamp,end_timestamp,seat_id,presence_label,posture_label,event_label,annotator
```

- `start_timestamp / end_timestamp`：相对秒 float64，与 raw.npz timestamps 同一坐标；
- `seat_id`：与 meta.json 一致；
- `annotator`：标注人匿名编号（如 D01/D02），双人抽检可追溯。

## 2. 枚举

### presence_label（与 result.csv 对齐）
`-1`（无法判断：遮挡/信号异常/看不清）、`0`（座位区域确认无人）、`1`（确认有人）。
**不能默认空位**：视频里座位被挡住时标 `-1`，不猜。

### posture_label
`reference`（个人参考范围）、`forward`（前倾）、`backward`（后靠）、`unknown`（过渡/无法判断）、
`not_applicable`（无人段）。仅在 presence=1 段使用前三者 + unknown；无人段一律 not_applicable。
参考段以标定记录（无姿态标签的 10–15 s 段）为准，不从测试姿态反推。

### event_label 词表 v0.1

| 事件 | 定义 | 建议标注粒度 |
|---|---|---|
| `enter` | 人进入座位区域并落座 | 事件点（起止各一） |
| `leave` | 人离开座位 | 事件点 |
| `bag_placed` | 书包/静态物放上座位 | 事件点 |
| `bag_removed` | 书包/静态物移走 | 事件点 |
| `passby` | 旁人从座位区域经过不停留 | 区间（经过全程） |
| `page_flip` | 翻书/翻页等短暂上肢动作 | 事件点 |
| `chair_move` | 挪椅/椅子位移 | 区间 |
| `sensor_block` | 传感器被遮挡/挡住 | 区间 |
| `still_motionless` | 静坐且几乎无动作（呼吸级微动除外） | 区间 |
| `reading` | 阅读状态（含偶发翻页） | 区间 |
| `posture_shift` | 明显姿态变化（前倾↔后靠↔坐直过渡） | 事件点 |

标注建议：10 s 粒度初标 presence/posture → 事件点秒级细化 → 双人抽检 ≥10% → 争议第三人仲裁。

## 3. split.json（人员级划分）

同一被采集人不跨训练/验证/测试集合（防人员泄漏）：

```json
{
  "schema_version": "0.1",
  "splits": {
    "train": ["P01", "P03"],
    "val":   ["P02"],
    "test":  ["P04", "P05"]
  },
  "note": "seat-level split also allowed; never split one person across sets"
}
```

## 4. 与 C 的对接（评价路径）

- 合成自测的真值是**剧本**（`<rid>_scenario_script.json`，命名刻意区别于 labels.csv）；
- 真实数据评价一律以 D 的 labels.csv 为准：g1_analysis 后续加 measured 模式
  （滑窗真值从 labels.csv 的 presence 区间读取，方法与合成剧本一致）；
- presence 区间评价 + event 时间点对齐（±1 s 容差建议）分别统计。

## 5. 待 D 确认事项

1. event 词表是否够用（是否要加喝水/玩手机/起身半站等）；
2. 抽检比例与仲裁流程；
3. 视频与雷达时间对齐方案（打板/闪灯事件，误差目标 ±0.5 s）；
4. posture_label 的"参考范围"如何展示（是否需要每段参考区间标注）。
