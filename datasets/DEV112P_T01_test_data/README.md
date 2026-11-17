# DEV112P · T01 实测交接包

内含 12 段真实录制。formal 下有 6 组正式测试，retests 下有 2 组短复测和 1 组空椅移位对照，pilot 下有首次试采与两段短录制。试录仅用于读取与波形探索。

每段包含 raw.npz、meta.json 和采集记录.md。使用 NumPy 可直接读包。

~~~python
import numpy as np

with np.load("formal/T01_P01_R01/raw.npz", allow_pickle=False) as z:
    complete = z["valid_chirps"].all(axis=(1, 2, 3))
    samples = z["raw_data"][complete]
    t_s = z["nominal_time_s"][complete]
    iq = samples[..., 0].astype(np.float32) + 1j * samples[..., 1]
~~~

raw_data 的轴顺序为帧组、芯片、接收通道、chirp、距离 FFT 分箱、I/Q。数值为设备 1DFFT 整数计数。分箱用索引表示，米制映射待标定。实际零读数须与 valid_chirps 指示的缺包填充值区分。

nominal_time_s 以秒为单位，按设备配置的帧周期推算。秒表事件相对点击「开始」计时，与雷达时间的对齐误差未测量。P01、P03 没有实测动作时间。计划口令仅供参考。现有 v1 交接工具要求逐帧实测时间，这套包使用独立归档格式。

| 录制 | 内容 | 帧组 | 有效数据包 | 实测秒表事件数 |
| --- | --- | ---: | ---: | ---: |
| [T01_P01_R01](formal/T01_P01_R01) | P01 正式坐姿流程 | 840 | 214,820 | 0 |
| [T01_P02_R01](formal/T01_P02_R01) | P02 正式坐姿流程 | 801 | 204,896 | 6 |
| [T01_P03_R01](formal/T01_P03_R01) | P03 正式坐姿流程 | 797 | 203,956 | 0 |
| [T01_P04_R01](formal/T01_P04_R01) | P04 正式坐姿流程 | 807 | 206,356 | 6 |
| [T01_neighbor_R01](formal/T01_neighbor_R01) | 邻座活动，目标座全程空着 | 323 | 82,564 | 3 |
| [T01_aisle_R01](formal/T01_aisle_R01) | 过道走动，目标座全程空着 | 313 | 79,852 | 5 |
| [T01_P02_retest](retests/T01_P02_retest) | P02 短复测 | 497 | 127,160 | 5 |
| [T01_P04_retest](retests/T01_P04_retest) | P04 短复测 | 504 | 128,796 | 5 |
| [T01_chair_shift](retests/T01_chair_shift) | 空椅移位对照 | 577 | 147,652 | 4 |
| [pilot_01](pilot/pilot_01) | 首次非正式试采 | 349 | 89,276 | 0 |
| [short_01](pilot/short_01) | 复测前短录制（有缺帧） | 113 | 28,454 | 0 |
| [short_02](pilot/short_02) | 空椅对照前短录制 | 54 | 13,552 | 0 |
