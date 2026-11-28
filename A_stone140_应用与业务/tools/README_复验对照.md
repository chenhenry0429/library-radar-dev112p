# A 端同录制复验对照

`compare_replay.py` 使用仓库共享的 A 状态机重放同一录制的两版 C 结果。它校验录制编号、帧序和时间戳，用同一份 A 配置生成两套时间线、事件 CSV 和 `comparison.json`。命令行会显示确认空座、确认占用、启动倒计时、取消倒计时和释放的帧号。比较基于帧内时间，回放到文件末尾即停止。

需要 Python 3.12 和 GitHub 项目仓库的 A 应用目录与共享契约。在仓库根目录运行 `python -m pip install -r requirements.txt` 安装依赖。

```powershell
python -X utf8 ".\A_stone140_应用与业务\tools\compare_replay.py" `
  --meta "<同录制_meta.json>" `
  --baseline "<旧版_result.csv>" --baseline-algorithm "<旧版_algorithm_online.json>" `
  --candidate "<新版_result.csv>" --candidate-algorithm "<新版_algorithm_online.json>" `
  --config "<offline_config.json 或 A_field_snapshot.json>" `
  --output "<新建输出目录>"
```

两版默认要求帧数相同。若现场记录只包含完整归档的前 907 帧，可加 `--frame-limit 907`。输出会保留两份输入各自的总帧数和实际比较帧数。每版提供自己的算法描述，方便追踪版本。保持现场配置不变时，可直接传 A 的现场状态快照，脚本读取其中的 `config` 并核对录制编号。

T01 第二轮使用旧版归档和 `6e06fd3` 独立复跑结果，固定 A 的 0.60 门槛和 3 秒连续判空。基线未启动倒计时，候选在第 850 帧启动、第 868 帧取消，两版在录制结束前均未释放。[实测回放复验记录](../docs/2026-11-28_T01第二轮A端实测回放复验.md)

仓库回归使用可公开的合成录制。

```powershell
python -X utf8 ".\A_stone140_应用与业务\tools\selftest_compare_replay.py"
```
