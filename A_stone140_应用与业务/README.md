# A · stone140 · 应用与业务

负责人：[@stone140](https://github.com/stone140) · [全组成果看板](../WORK_STATUS.md)

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
