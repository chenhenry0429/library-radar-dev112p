DEV112P 离线接口 v1：组员接收说明

1. 用 U 盘、移动硬盘或聊天文件传输拿到 ZIP，完整解压。无需运行雷达、串口或联网服务。
2. manifest.json 记录所有交付文件的 SHA-256，用于发现传输损坏；它不是身份签名。
   用 Python 3.10+ 运行：python verify_transfer.py .
   也可直接检查未解压的 ZIP：python verify_transfer.py "交付文件.zip"
   校验本身只用 Python 标准库，不依赖 NumPy。
3. 读取数据需要事先装好 Python 3.10+ 和 NumPy（已在 Python 3.12 / NumPy 2.3.5 验证）。
   本包不包含完整 Python 运行环境。若电脑完全离线且尚无环境，先由组内准备相匹配的离线安装包。
4. 运行：python read_data.py "20261103_PDEMO_S01_lifecycle_01"
   Windows 可用：run_python.cmd read_data.py "20261103_PDEMO_S01_lifecycle_01"
5. C 的最小读取代码：
   from contract import load_b
   arrays, meta = load_b("采集目录")
   raw = arrays["raw_data"]
   timestamps = arrays["timestamps"]
   frame_ids = arrays["frame_ids"]
   不强制二维，不丢弃复数部分，不使用 allow_pickle=True。
6. 文件夹名、所有文件名前缀、meta.recording_id 必须一致。
   source_kind=synthetic 的包只能联调；不能当成实测、不能用于识别准确率或坐姿效果评价。
7. B 包包含 raw.npz + meta.json；B+C 包另含 result.csv + algorithm.json。
   D 的 labels.csv 单独保管，不能输入 C 推理或 A 状态机。
8. 正式数据必须核实 data_level、各轴定义、dtype、单位、IQ约定和真实时间来源。
   本演示的 raw 是人工生成的数字，不代表 DEV112P 的任何输出格式。

软件不会自动把文件发送给任何人。由 B 校验完成后手动交付整个 ZIP。
