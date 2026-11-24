# B · chenhenry0429 · 雷达与采集

负责人：[@chenhenry0429](https://github.com/chenhenry0429) · [全组成果看板](../WORK_STATUS.md)

## DEV112P 实时联调

[采集和解码入口](dev112p_live/a_device_bridge.py) 使用 ONELAB 正在写入的 1DFFT 文件。现场在 ONELAB 选择已验证的 COM5 设备和保存目录。A 点「开始检测」后启动 B、C，再在 ONELAB 点「开始」。结束时先在 ONELAB 点「停止」，再在 A 点「停止检测」，使桥接程序保存本轮原始数据和日志。

使用安装了仓库依赖的 Python 3.12，在仓库根目录运行下面的命令。将参数替换为采集电脑上的路径，并使用 C 的 `9c34389` 版本。

```powershell
python -X utf8 .\B_chenhenry0429_雷达与采集\dev112p_live\a_device_bridge.py `
  --recordings "<ONELAB 保存目录>" --output "<实时结果目录>" `
  --c-root "<C 的 seat-detection 目录>" --config "<C 的 M1_dev112p_v1.2.1.json>" `
  --device-config ".\A_stone140_应用与业务\radar_device.local.json" `
  --reference-ini "<已验证录制中的 FILE_Radar_Config.ini>"
python -X utf8 .\A_stone140_应用与业务\live_monitor.py --port 8766
```

打开 `http://127.0.0.1:8766/` 的「使用者」页。首次等待窗口为 60 秒。每次启动生成新录制编号，B 保留原始流、有效 chirp 掩码和设备帧号。C 使用既有九字段接口串行向 A 发送并等待回执。结果目录下的 `A_delivery.json` 记录 B 保存帧数和 A 接收帧数。

时间轴使用从首帧起按设备计数推算的名义相对秒。B 同时记录主机文件读取观测时间。现场动作秒表与雷达首帧的偏移需要单独测量。

## 离线处理

| 成果 | 唯一运行文件 |
|---|---|
| 已解码数组导入、阶段校验、离线打包 | [radar_offline.py](../radar_offline.py)：import-decoded / validate / pack |
| 原始文件字段、维度、时间和逐帧校验 | [contract.py](../contract.py)，与 A/C/D 共享 |
| 组员离线读取 | [read_data.py](../read_data.py) |
| ZIP / 文件夹完整性检查 | [verify_transfer.py](../verify_transfer.py) |
| 配置模板 | [meta_template.json](../meta_template.json) |
| 操作手册与接收说明 | [PDF](../docs/operation-manual.pdf)、[组员接收说明](../README_组员离线读取.txt) |

这些工具属于仓库初始公共基线，保留在根目录以兼容既有命令和离线交付包。

## 下一步：设备到货前

- [ ] 收齐适配板卡/固件的手册、数据协议、工具与授权说明、真实样例。
- [ ] 核实供电、电平、引脚、连接配件和数据输出层级。
- [ ] 准备桌组/座位编号、尺寸、安装点与采集场景记录。
- [ ] 让另一名组员在自己的电脑校验并读取模拟交付 ZIP。

## 下一步：设备到货后

- [ ] 完成连接和真实接收；当前尚无 DEV112P 串口抓帧/协议解析实现。
- [ ] 明确真实载荷的维度、dtype、单位、IQ、通道及时间来源。
- [ ] 顺序采集空场、静坐、走动、邻座/过道干扰。
- [ ] 校验 `--stage b` 通过，再交给 C；不把未知透传字节当 ADC，不补造采样时间。

输入：按真实协议解码的 numeric 数组、逐帧时间和完整 meta。输出：同目录下 `<recording_id>_raw.npz` 与 `_meta.json`，以及离线交付 ZIP。
