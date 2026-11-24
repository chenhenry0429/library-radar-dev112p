# C 现场入口（radar_field）交付说明

- 日期：2026-11-24｜对应 A《2026-11-24 A_B_C 一键检测真机联调》里 C 的交付项
- 入口：`tools/radar_field.py`（前台运行）；联调模拟器 `tools/fake_b_stream.py`；
  自测 `tools/selftest_field.py`
- 能力边界：本轮只交付**单座占用**（`capabilities.posture=false`）；坐姿在线化不在本轮

## 1. 启动方式

A 页面「开始检测」按 `radar_device.local.json` 的 `command` 启动本入口，并注入：

| 环境变量 | 含义 |
|---|---|
| `RADAR_A_FRAME_URL` | A 的逐帧接收地址（如 `http://127.0.0.1:8766/api/frame`） |
| `RADAR_A_FRAME_TOKEN` | Bearer 令牌（每轮新令牌、新会话） |
| `RADAR_A_META` | 现场会话 meta 路径 |
| `RADAR_A_ALGORITHM` | `_algorithm_online.json` 路径（A 启动时读取） |

现场命令示例（`command` 里写 `python` 的绝对路径）：

```powershell
python -X utf8 tools/radar_field.py ^
    --source-cmd "\"C:\Program Files\Python312\python.exe\" B目录\detect_live.py" ^
    --config config/M1_dev112p_v1.2.1.json --emit-scores
```

也可以由 B 程序通过管道喂入（不带 `--source-cmd`）：

```powershell
B程序 --jsonl | python -X utf8 tools/radar_field.py --config config/M1_dev112p_v1.2.1.json --emit-scores
```

**先准备算法说明**（A 启动时就要读，必须在点「开始检测」之前）：

```powershell
python -X utf8 tools/radar_field.py --meta 现场_meta.json --algorithm 现场_algorithm_online.json ^
    --config config/M1_dev112p_v1.2.1.json --emit-algorithm
```

入口在启动时会校验 `algorithm.recording_id == meta.recording_id`；不一致直接报错退出
（两份描述必须指向同一会话）。文件缺失时会自动生成并提示重启 A。

## 2. B→C 帧格式（DevFrame，JSON Lines）

一行一个对象（也接受 `{"type":"meta",...}` 行，会被忽略）：

```json
{"type":"frame","frame_id":0,"timestamp":0.0,
 "raw_frame":[[[[[1,2],...]]]],          // int16 (chip,rx,chirp,range_fft_bin,iq)
 "valid_chirps":[[[true,...]]]}           // bool  (chip,rx,chirp)
```

大帧推荐紧凑编码（`tools/fake_b_stream.py` 默认用它）：

```json
{"type":"frame","frame_id":0,"timestamp":0.0,
 "raw_frame_b64":"<int16 LE, C order>","raw_shape":[2,2,64,8,2],
 "valid_chirps_b64":"<uint8 0/1, C order>","valid_shape":[2,2,64]}
```

规则（与《2026-11-19 B↔C 帧级接口提案》一致）：

- `frame_id` 从 0 起严格连续；跳号 = 协议错乱，入口报错退出（与 A 契约一致）。
- `timestamp` 为采集起点相对秒、严格递增；缺省用 `frame_id × nominal_frame_period_s`。
- 投影（chip/rx 选路、奇偶 chirp 合并）留在 C；B 只交原始立方 + 掩码。
- 丢帧/坏帧请发**同号全零 `valid_chirps`** 帧：C 输出 `invalid_frame`（presence=-1），
  不推进识别状态机，A 侧按 unknown 处理。
- 时间基：`seconds / recording_start`；名义帧周期由 meta 提供（0.1935 s）。

## 3. C→A

复用既有九字段契约与串行 POST：每帧等到 `accepted_frame_id` 与帧号一致才发下一帧；
HTTP 4xx 不重试立即中止，网络错误指数退避并先 `GET /api/state` 对齐（防重复帧）。
每轮从帧 0 开始，新采集 = 新会话。

## 4. 资源释放（A 清单里的「停止检测」项）

`radar_field` 用 `--source-cmd` 启动 B 时**拥有该子进程**。以下任一情况都会进入清理：

- B 的 stdout EOF（雷达停止）；
- 用户 Ctrl-C / A 发送停止（SIGTERM/终止）；
- 异常退出。

清理动作：`B.terminate()` → 等 5 s → `kill()`；关闭管道；写
`<rid>_result.csv`、`<rid>_scores.csv`（可选）、`<rid>_session.json`；打印帧数/告警。
「停止检测」后再次开始 = 重新启动入口 + 新令牌 + 帧 0。

## 5. 验证

| 验证项 | 结果 |
|---|---|
| 合成场景 E2E（430 帧，空座→静坐→走远） | 入口 0 帧 → 21.48 s 入场 → 74.9 s 判空；430 帧全部 accepted；B 子进程被终止 |
| 零掩码帧 | 该帧 `invalid_frame`、presence=-1；前后状态机状态不变 |
| `recording_id` 不一致 | 启动即报错退出 |
| **真实归档 T01_P04_retest（504 帧原始立方）** | 与离线 `detect.py`（v1.2.1）**presence 逐帧一致、timestamp 逐位一致** |
| 算法说明生成 | `recording_id`、`capabilities={"presence":true,"posture":false}` 正确 |

自测命令：`python -X utf8 tools/selftest_field.py`（无需硬件，CI 已加）。

## 6. 与 A 的真机验收清单对应

| A 的验收项 | C 侧对应 |
|---|---|
| 点「开始检测」后帧号从 0 连续更新 | 入口从帧 0 接收；跳号即报错 |
| 空座/入座/静坐/离座/释放的页面反馈 | 沿用 v1.2.1 阈值与九字段证据（见对照表报告） |
| 拔设备/中断采集 | B EOF → 入口清理退出；A 侧按 `data_gap_s` 显示中断 |
| 20 s 未收首帧提示 | A 的页面规则；入口在无帧时打印 warn |
| 点「停止检测」后资源释放、再次开始从帧 0 | 入口终止 B 子进程并落盘；重启即帧 0 |

## 7. 待 B 确认/待现场

1. B 的解码程序入口与输出方式（本入口已定义 JSONL 契约，B 只需按行输出）；
2. B 的帧时间来源（名义 0.1935 s 步进，或采集相对秒）；
3. 现场 `_meta.json`（区域 `[1,3]`、`main_bin=2`、`nominal_frame_period_s`）与
   `_algorithm_online.json` 的准备时机（A 启动前）；
4. 姿态在线化（`capabilities.posture=true`）留待 M2 在线化版本。
