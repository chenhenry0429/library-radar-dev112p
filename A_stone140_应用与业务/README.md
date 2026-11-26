# A · stone140 · 应用与业务

负责人：[@stone140](https://github.com/stone140) · [全组成果看板](../WORK_STATUS.md)

## 统一监测入口 v0.5.0

在 A 目录运行 `python live_monitor.py`，打开终端中的本机地址。首页默认进入「使用者」，显示座位空闲、使用中、暂时离座、待确认和坐姿提醒。「开发者监测」查看同一实时数据的信号、异常、标定、会话、识别证据和事件。「离线回放」保留文件导入和验收场景。现场模式通过本机 HTTP 接收 C 的逐帧结果，演示模式使用合成录制。

使用者页面新增「开始检测」和「停止检测」。在采集电脑配置实时采集与识别入口后，按钮可启动本机程序，接收首帧后显示「正在检测」。Windows 采集电脑完成 ONELAB 设备和录制按钮配置后，可由页面启动和停止采集。真实设备首帧仍待现场验收。

三个标签共用低照度配色。开发者监测首屏显示当前反馈和收帧时效，离线回放在同一区域展示播放控件、占用轨道和已有提醒标记。浏览器验收覆盖 1440、390 和 320 px，以及内置演示和实测样例文件导入。ONELAB 开停、首帧和实际断流仍需现场联调。

[Windows 真机配置步骤](README_实时入口.md#windows-采集电脑配置-dev112p) · [真机对接与验收清单](../对接事宜/2026-11-24_A_B_C一键检测真机联调.md) · [启动与 C 接入说明](README_实时入口.md)

## 已提交的成果

- [x] [离线回放页面](offline_viewer/index.html)：查看已有的占用、姿态、标定、提醒和事件。
- [x] [内置演示数据](offline_viewer/demo-data.js)：用于页面联调。
- [x] [页面使用说明](offline_viewer/使用说明.txt)：文件选择、播放和校验范围。

以上页面成果来自 stone140 已合入的 [f78133f](https://github.com/chenhenry0429/library-radar-dev112p/commit/f78133fd8e33616110529334ac6138850a04c397)。本次移动保持页面和演示数据内容不变。

公共状态处理基线继续位于 [offline_state_machine.py](../offline_state_machine.py)，由根目录 [radar_offline.py](../radar_offline.py) 的 `run-a` 调用。它处理占用会话、离座等待、释放、姿态累计、冷却和复核，不是本次前端提交新增的分类器。

## 怎么打开

下载并完整解压仓库，双击 `offline_viewer/index.html`。`index.html` 与 `demo-data.js` 要放在同一目录。页面可载入内置演示，或导入同一次 A 运行的三个输出文件。

从仓库根目录生成 A 输出的例子：

```powershell
python radar_offline.py run-a DEMO_ONLY/release_demo/20261103_PDEMO_S01_lifecycle_01 --config DEMO_ONLY/release_demo/demo_config.json --output output --run-id viewer_check_01
```

随后在网页同时选择该次输出的 `timeline.csv`、`events.csv`、`run.json`。不要直接将 C 的 `_result.csv` 当成 A 的时间线。

## 输入与输出

输入：C 的 result/meta/algorithm 和 A 的配置；原始 NPZ 仅用于对应校验。

输出：`output/<recording_id>/<run_id>/` 下的 timeline/events/run。页面回放已有输出，不重新推理、不播放声音、不上传本地文件。

## 下一步

- [ ] 用 B/C 的实际交付文件检查整条离线链路。
- [ ] 核对未知、待确认、人工复核、标定失效、EOF 和不同回放速度。
- [ ] 在真实多座接口确定后，再做多座位展示；目前页面按单座运行结果读取。

与 D 的协作：虽然由同一人负责，D 的评价标签仍不能进入 A 的业务运行逻辑。

## 当前页面版本：v0.2.0（2026-11-17）

新增座位概览、可用统计、异常提示与历史、筛选和三组验收场景；占用参数不变。保留根目录共享状态机和旧 run.json 兼容。

- [版本记录](offline_viewer/CHANGELOG.md) · [逐项验收](docs/v0.2.0_页面逐项验收.md) · [固定参数快照](docs/v0.2.0_固定参数快照.json)
- 完整下载仓库后打开 offline_viewer/index.html；相邻 JS 文件须保持完整，页面底部显示 v0.2.0。
- Node.js 18+：在本目录运行 `node offline_viewer/test-acceptance.cjs`。18 项显示逻辑断言可在其他电脑独立运行，结果写入 offline_viewer/test-results/。
- fixtures/v02 是 C d3370f4 占用基线派生的 A 输出，用于解析/显示检查，不是独立真值，也不是实时 M1 或坐姿验收。
- 本次测试中的后端 18 项是 A 本地回归证据，后端代码不随页面发布；不要据此声称仓库根目录共享状态机已由这些测试完整验证。
- 合成异常显示已自动检查；浏览器视觉、真实文件选择器、断连、离座和干扰实测仍待验收。

## A / D 核对补充（2026-11-18）

stone140 同时负责 A 与 D；[D 工具 v0.1.0](../D_stone140_标注与评价/README.md)已补齐，标签不输入本应用。

新增 [NPZ 对齐诊断](tools/check_npz_alignment.py)（需要 NumPy），参数依次为 raw.npz、result.csv、meta.json、输出 JSON。严格相等时退出 0；仅数值接近/不匹配时退出 2，避免误当成严格通过。[P01 诊断](docs/2026-11-18_P01_NPZ对齐诊断.json)的最大时间差约 2.84e-14 秒，仍需 C 保留 float64 往返精度重导出。

页面自动测试 18 项再次通过。浏览器本地 file:// 访问被工具策略拒绝，视觉与文件选择器验收继续待人工，不能标成完成。详见[本轮 A/D 核对](../对接事宜/2026-11-18_A_D待办核对.md)。
