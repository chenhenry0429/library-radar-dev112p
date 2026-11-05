# B · 雷达与采集

**当前实现**：[radar_offline.py](../../radar_offline.py) 的 import-decoded / validate / pack，[read_data.py](../../read_data.py) 和 [verify_transfer.py](../../verify_transfer.py)。未实现串口抓帧或 DEV112P 透传解码。

输入：真实协议已解码的固定形状 numeric 数组、真实逐帧时间，以及核实完整的 meta。模板：[meta_template.json](../../meta_template.json)。

输出：同名目录下 recording_id_raw.npz + recording_id_meta.json；打包 B 阶段文件和组员接收辅助程序。

下一项任务：取得适配板卡/固件的协议与工具说明，核实供电/电平/引脚/帧格式，完成一个座位的实测。未知透传字节不命名为 ADC，不用标称帧率伪造时间。

采集顺序：空场 -> 静坐 -> 走动 -> 邻座/过道干扰。每段单独编号、记录安装及配置，保留无效和时间间隔，不因标签删除帧。

验收：载荷级别、维度、dtype、单位、IQ及真实时间来源明确；严格单调时间；validate --stage b 通过；组员从 ZIP 解压后能校验并读取。操作见 [PDF](../../docs/operation-manual.pdf)。
