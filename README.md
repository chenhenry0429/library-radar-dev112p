# 图书馆座位占用与坐姿提醒 · DEV112P

用毫米波雷达判断图书馆自习座位是否有人、何时离座。项目先在有高隔板的自习桌上验证单个座位，后续再验证相邻座位，并探索坐姿变化提醒。

**当前阶段：T01 单座实测与离线回放。** 已采集 **12 段真实录制**。首段 P01 的 **840 帧**已完成解码，基于这段录制整理的占用基线已通过 A 端本地状态机复验。真实占用识别准确率、坐姿和多座并发仍待验证。

[下载页面演示](https://github.com/chenhenry0429/library-radar-dev112p/archive/refs/heads/main.zip) · [看真实数据索引](https://github.com/chenhenry0429/library-radar-dev112p/blob/data/dev112p-t01-recordings/datasets/DEV112P_T01_test_data/README.md) · [读 P01 复验记录](对接事宜/2026-11-17_C占用基线v0.2_A端复验.md) · [看进度与分工](WORK_STATUS.md)

## 系统怎么工作

~~~mermaid
flowchart LR
    B["B · 雷达采集<br/>记录原始回波"] --> C["C · 识别算法<br/>输出占用证据"]
    C --> A["A · 座位状态<br/>生成回放与事件"]
    C --> E["计算误报、漏报和延迟"]
    D["D · 独立标注<br/>记录现场座位状态"] --> E
~~~

## 真实数据验证到哪一步

| 验证内容 | 已有结果 | 证据 |
| --- | --- | --- |
| **采集与解码** | 6 组正式测试、2 组短复测、1 组空椅移位对照、3 段试录。正式测试含邻座和过道干扰。 | [12 段录制及数据字典](https://github.com/chenhenry0429/library-radar-dev112p/blob/data/dev112p-t01-recordings/datasets/DEV112P_T01_test_data/README.md) |
| **占用基线回放** | P01 离线基线在名义时间 **19.74 秒**由空座切到有人。A 端本地复验处理了 840 帧，生成时间线和事件文件。 | [C 交接说明](C_cy071210_识别算法/outputs/t01-p01/handoff/INTEGRATION_NOTES.md) · [A 复验记录](对接事宜/2026-11-17_C占用基线v0.2_A端复验.md) |
| **识别算法** | M1、B0 已能读取实测录制并生成结果。当前 M1 在 P01 上有空座误报和静坐漏检，B0 主要提供走动等运动证据。P01 坐姿输出为未知或不适用。 | [首轮算法报告与图](C_cy071210_识别算法/outputs/t01-p01/verification_report.md) |

P01 的切换时刻来自离线回放基线，时间按帧周期推算。该录制缺少独立动作标注，当前的复验只覆盖一次入座及后续有座状态。真实离座释放、识别准确率和实时响应需另行验收。

## 怎么看页面演示

1. [下载仓库 ZIP](https://github.com/chenhenry0429/library-radar-dev112p/archive/refs/heads/main.zip) 并完整解压。
2. 双击 `A_stone140_应用与业务/offline_viewer/index.html`。
3. 点击 **载入模拟演示**，查看座位状态和事件随时间变化。页面内置示例为合成数据。

[应用使用说明](A_stone140_应用与业务/README.md) · [操作手册 PDF](docs/operation-manual.pdf)

<details>
<summary>运行命令行演示</summary>

使用 Python 3.12，在仓库根目录运行：

~~~powershell
python -m pip install -r requirements.txt
python selftest.py
python radar_offline.py demo --output DEMO_ONLY/my_first_demo
~~~

输出为同一次运行的 `timeline.csv`、`events.csv` 和 `run.json`，可一起导入回放页面。Windows 也可运行 `run_demo.cmd`。

</details>

## 接下来验证什么

1. **单座占用**：用有秒表记录的 P02、P04 标注静坐和离座区间，检查 M1 改进前后的漏报、误报和确认延迟。
2. **真实数据联调**：复核 NPZ 与时间线逐帧对齐，再验证实际离座和座位释放。
3. **坐姿与多座**：单座占用通过后，独立验证坐姿变化和相邻座位串扰。

[分工与待办](WORK_STATUS.md) · [成员与贡献](TEAM.md) · [项目架构](docs/architecture.md) · [共享字段](shared/README.md) · [协作说明](CONTRIBUTING.md)
