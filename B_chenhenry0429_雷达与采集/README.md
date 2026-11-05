# B · chenhenry0429 · 雷达与采集

负责人：[@chenhenry0429](https://github.com/chenhenry0429) · [全组成果看板](../WORK_STATUS.md)

## 已有成果与文件位置

| 成果 | 唯一运行文件 |
|---|---|
| 已解码数组导入、阶段校验、离线打包 | [radar_offline.py](../radar_offline.py)：import-decoded / validate / pack |
| 原始文件字段、维度、时间和逐帧校验 | [contract.py](../contract.py)，与 A/C/D 共享 |
| 组员离线读取 | [read_data.py](../read_data.py) |
| ZIP / 文件夹完整性检查 | [verify_transfer.py](../verify_transfer.py) |
| 配置模板 | [meta_template.json](../meta_template.json) |
| 操作手册与接收说明 | [PDF](../docs/operation-manual.pdf)、[组员接收说明](../README_组员离线读取.txt) |

这些工具属于仓库初始公共基线，保留在根目录以兼容既有命令和离线交付包。本目录负责把 B 的工作、位置和验收集中展示，不另复制一套程序。

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
