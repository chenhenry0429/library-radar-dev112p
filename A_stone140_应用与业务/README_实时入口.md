# A 端实时入口 v0.3.0

## 启动本机演示

双击 `启动座位监测.cmd`，打开窗口中显示的 `http://127.0.0.1:8766/`。首页默认进入「使用者」，显示座位状态、暂时离座倒计时和坐姿提醒。用顶部的「开发者监测」查看详细信号、异常和事件，用「离线回放」导入既有运行结果或打开验收场景。切换标签会保留回放状态。启动脚本默认以 5 倍速回放合成录制，页面会显示数据来源。关闭并重启命令窗口可以重新播放。

先在仓库根目录执行 `python -m pip install -r requirements.txt`，使用仓库约定的 Python 3.12 和共享接口依赖。以下命令可从 A 目录按原始记录间隔演示。

```powershell
python live_monitor.py
```

默认使用仓库 `DEMO_ONLY/release_demo` 下的合成录制。也可用 `--replay 路径_result.csv` 指定既有 C 结果，默认查找同目录、同前缀的 `_meta.json` 和 `_algorithm.json`。P01 v0.2 基线的名义时间可用于回放，页面标注时间来源。`--speed 5` 只压缩回放等待时间，状态机仍按数据时间计时。需要核对 NPZ 时使用原有离线校验入口。

## C 接入约定

在采集电脑上同时运行 C 和 A。A 启动时读取一次采集与算法说明，使用仓库共享的九列校验和状态机。默认参数取自 A 目录的 `offline_config.json`。在线输入使用共享 v1 的时间约定 `seconds / recording_start`，算法能力使用 `{"presence": true, "posture": false}` 形式，具体字段见仓库根目录 `contract.py`。

```powershell
python live_monitor.py --live --meta 路径_meta.json --algorithm 路径_algorithm.json
```

C 计算出一帧结果后，向 `POST http://127.0.0.1:8766/api/frame` 发送一条 JSON。请求头为 `Content-Type: application/json` 和 `Authorization: Bearer <令牌>`。令牌在 A 启动时显示，也可由启动环境预先设置 `RADAR_A_FRAME_TOKEN`。发送方等待成功响应后再发下一帧。

```json
{
  "frame_id": 0,
  "timestamp": 0.0,
  "presence": 0,
  "presence_confidence": 0.9,
  "target_count": 0,
  "posture": "not_applicable",
  "posture_confidence": null,
  "calibration_status": "uncalibrated",
  "quality": "ok"
}
```

使用原离线 CSV 的九个字段。帧号从 0 连续递增，时间是严格递增的采集相对秒。处理不了的帧保留原帧号与时间并给出未知，不把它填成无人。接口返回 `accepted_frame_id`。收到 400 时根据错误修正当前帧；若不确定请求是否成功，可先查询 `GET /api/state` 的 `state.frame_id`。新录制或采集重启导致帧号归零时，重新启动 A 与 C 的本次会话。

页面每约 400 毫秒从 `GET /api/state` 取得最新状态。超过配置中的数据缺口阈值未收到新帧时，页面显示待确认并冻结计时。恢复时重新确认占用。回放结束保留最后记录。页面显示近期事件，当前版本的会话和事件保存在进程内。实时标定记录在本版随算法说明载入，现场动态标定协议留待 C 联调。

## 实时反馈

实时标签显示当前异常与姿态提醒、座位可用与待确认统计、个人标定、姿态累计、释放倒计时和占用会话。收帧时效和当前 C 端占用、姿态、置信分数与质量同步更新。断流期间会冻结计时并保留最后确认状态，恢复接收后按有效证据重新确认。

事件可按异常与恢复、姿态提醒和占用变化筛选。页面保留最近 100 条事件。断流记录以最后收到的帧时间为参照。展开「运行来源与参数」可查询参数版本、生效配置和算法已知问题。当前事件缓存在进程内，现场验收仍需连接 B 和 C 的实时程序。
