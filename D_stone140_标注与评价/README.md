# D · stone140 · 独立标注与评价 v0.1.0

stone140 同时负责 A 与 D。评价标签独立保存，不进入 C 推理、标定选择或 A 状态机。

已补齐：现场记录标注生成器、七列标签校验器、人员划分校验、占用评价程序和合成回归测试。标注规则见 [标注细则](标注细则_v0.1.md)。Python 3.12，基础工具只用标准库。

在 D 目录执行：

```powershell
python prepare_annotations.py 现场记录根目录 labels_local/field_v0.1.0
python label_tools.py validate labels_local/field_v0.1.0/T01_P02_R01/labels.csv
python -m unittest -v test_evaluation
```

现场记录根目录应包含 formal/T01_P02_R01、T01_P04_R01、T01_neighbor_R01、T01_aisle_R01，以及 retests/T01_P02_retest、T01_P04_retest、T01_chair_shift；每处含 B 的 meta.json、采集记录.md。工具不读取 raw 或 result 来生成标签。

待已验证时间对齐且标注复核后评价：

```powershell
python evaluate_presence.py 采集_result.csv labels.csv annotation.json 采集_meta.json 采集_algorithm.json reports_local/本次评价 --max-gap-s 0.5
```

时钟偏移、标注尚未复核时默认拒绝评价。如仅用于调试，可显式加 --allow-provisional --offset-s 数值，报告强制标为 provisional_not_acceptance；该数值表示 prediction_time = label_time + offset_s。不得把 offset=0 的假设当成已测同步。

报告保存分母、未知/拒识、排除区间、输入哈希、算法参数版本；缺口不填补、末帧不外推。事件延迟不计算，除非以后提供独立动作起点和时间对齐实现。

2026-11-18：依据数据分支的 B 独立现场记录，在 A/D 本地整理了上述 7 组 labels/annotation。P02/P04 等秒表时钟待对齐，邻座/过道按现场确认常量空座。真实标签和个人记录按仓库约定保留本地，不进入公开 main。当前没有这些录制对应的 C 预测文件，不报告真实算法准确率。

既有 P01–P04 已用于开发，split 中全列 development；独立 validation/test 为空，冻结后需新参与者/录制。生成器不回头假设已看过数据属于独立测试。

待办：B 确认时钟/动作记录，D 人工复核标签，C 交付同录制的预测及说明，再运行独立评价。P01/P03 不从 C 的基线边界反造真值。
