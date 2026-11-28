# 484 帧现场在线抓包 · v1.2 vs v1.2.1 复跑证据

- 数据来源：组员 2026-11-21 提供的 `T01_484_C_replay.zip`
  （源录制 `2026_11_19_20_12_57`，T01，20 cm 候选安装位置，484 帧 / 93.4605 s）
- 秒表（现场记录，provisional）：**坐稳 29.48 s**、**走远 58.06 s**
- 输入：`examples/live_v121/T01_http_live_20261119_201257_965862_c1r0_1dfft_parity_raw.npz`
  （奇偶分链）与同目录 `_1dfft_raw.npz`（v1.2 单链）

## 本目录文件

| 路径 | 内容 |
|---|---|
| `<rid>_result.csv` / `_algorithm.json` / `_scores.csv` | v1.2.1 **修复后**三件套（本次提交，`M1 v1.2.1`） |
| `before_fix/replay_summary.json` | 组员用修复前 v1.2.1 复跑的汇总（**11.61 s 开场误判**、67.53 s 释放） |
| `before_fix/v121_result.csv` / `v121_scores.csv` | 组员复跑（修复前）的逐帧输出，用于对照 |

## 三个验收项（修复后）

| 检查项 | 结果 |
|---|---|
| 开场为空 | presence=0 直到 **27.48 s**（坐稳前 2.0 s，人到座）；11.61 s 误判已消除 |
| 静坐保持有人 | 29.48–58.06 s 共 142/142 帧 presence=1 |
| 走远后恢复空座 | 1→0 于 **67.53 s**（走远后 9.47 s），此后至 93.46 s 全 0 |

复跑：

```powershell
python -X utf8 detect.py --recording examples/live_v121/T01_http_live_20261119_201257_965862_c1r0_1dfft_parity_raw.npz \
    --config config/M1_dev112p_v1.2.1.json --emit-scores --out <out>
```
