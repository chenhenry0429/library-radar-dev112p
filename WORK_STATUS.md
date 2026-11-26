# 项目进度与分工

更新于 2026 年 11 月 26 日。当前里程碑是 **T01 单座实测**。12 段录制已经归档，P01 的 840 帧占用基线通过 A 端本地复验。下一阶段要用有独立标签的录制验证静坐、离座和干扰。

| 负责人 | 已完成 | 下一项交付 |
| --- | --- | --- |
| **A 应用 · stone140** | 离线查看器 v0.2.0 已加入单座位概览、异常提示/历史和验收场景；18 项显示逻辑检查通过。P01 v0.2 基线输出样例随页面测试夹具提供。A 统一监测入口 v0.5.0 加入本机启停反馈，三个标签已统一视觉。浏览器验证了桌面与手机布局、内置演示、回放定位及结果文件导入。[Windows 真机配置](A_stone140_应用与业务/README_实时入口.md#windows-采集电脑配置-dev112p) · [真机联调清单](对接事宜/2026-11-24_A_B_C一键检测真机联调.md)[应用入口](A_stone140_应用与业务/README.md) · [复验记录](对接事宜/2026-11-17_C占用基线v0.2_A端复验.md) | P01 NPZ 数值对应检查已补，时间严格相等仍差浮点末位；请 C 保留往返精度。B 的 DEV112P 实时采集桥接已合入，C 在线依赖已纳入。C 的 JSONL 现场入口也已接入共享测试，验证对真实 A 服务的 430 帧回执。采集电脑仍需现场验收 ONELAB 开停、雷达首帧、占用变化、断流和重启。 |
| **B 采集 · chenhenry0429** | 完成 T01 六组正式测试、两组短复测、空椅对照和三段试录。原始 I/Q 数组、字段说明与记录已放入[独立数据分支](https://github.com/chenhenry0429/library-radar-dev112p/blob/data/dev112p-t01-recordings/datasets/DEV112P_T01_test_data/README.md)。 | 与 A、C 核对帧轴、时间来源及缺帧处理。 |
| **C 算法 · cy071210** | 12 段 T01 录制全部转换并以 v1.2 批量回放（selfcheck 12/12 通过，预测在 outputs/t01-batch 与 outputs/posture-v01，可直接进 D 评价流程）。M2 姿态 v0.1 门控基线上线：11/12 组被对比度门如实拒识（posture=unknown），P01 唯一过门但方向暂不可验证，见 [姿态诊断](C_cy071210_识别算法/outputs/posture-v01/posture_v01_diagnostics.md)。v1.2 钉死缺陷已量化：11 组空座误报 93.8–98.7%、离座 9/9 零释放，P01 为唯一幸存组（[缺陷分析](C_cy071210_识别算法/outputs/t01-round2/v1.2_round2_defect_analysis.md) · [验证报告](C_cy071210_识别算法/outputs/t01-devset-validation/devset_validation_v12_m2.md) · [能力与已知问题](C_cy071210_识别算法/seat-detection/CAPABILITIES_AND_KNOWN_ISSUES.md)）。**在线逐帧入口 v0.1 交付**：run_m1 重构为逐帧状态机并与离线逐字节一致（12/12），SeatDetector 会话入口 + B 单帧投影（与离线适配器字节等价）+ live_seat 逐帧转发驱动，POST 链路冒烟通过（[验证报告](C_cy071210_识别算法/outputs/online-v01/online_v01_验证报告.md)）。**A↔C 在线链路收口（2026-11-19）**：capabilities 语义成文（capabilities 只放 presence/posture 两个纯 bool，扩展语义进 notes）+ 丢帧/断流重连/会话重置/POST 失败四类规则索引与 C 侧补齐（网络退避重试 + GET /api/state 对齐防重复帧）；`--emit-algorithm` 产出 contract 新格式 `_algorithm_online.json`（detect.py 旧格式 A --live 不收）；C↔A 端到端预演（真 `live_monitor.py --live`，pilot_01 349 帧）**20/20 通过**，改造后 12 组在线回归零漂移（[收口文档](对接事宜/2026-11-19_A-C_capabilities语义与在线异常处理.md)，待 A 复核）。 | 等 B 确认[帧级接口提案](对接事宜/2026-11-19_B-C帧级接口提案.md)后在采集电脑现场联调（对接 A 实时入口）；A 复核[收口文档](对接事宜/2026-11-19_A-C_capabilities语义与在线异常处理.md)（capabilities 纯 bool 规范、algorithm_online.json 启动序列、data_gap_s/review_after_s 数值、C 侧单方重建边界情形）；v1.2.1 入座前背景/漂移修复方案（f1 开环标定改为自适应），等团队评审后实施；按[补采清单](C_cy071210_识别算法/outputs/posture-v01/posture_v01_diagnostics.md)配合 B 补采带方向标注的片段。 |
| **D 标注与评价 · stone140** | D 工具 v0.1.0 已补齐标注生成、校验、划分与占用评价，14 项测试通过；7 组现场记录标签包已在本地生成，真实标签不入 main。[标注约定](D_stone140_标注与评价/README.md) | 复核标签并与 B 确认秒表对齐；等待同录制的 C 预测后评价。既有参与者归开发集，真实准确率/延迟未计算。 |

## 验收顺序

1. **回放可以复现**：A 与 B、C 对齐 P01 数据和结果，保留名义时间来源。
2. **单座占用可以独立评估**：D 给出已有录制的评价区间，C 交付逐组误报、漏报以及事件延迟。固定参数后，用新录制复验。
3. **扩展能力**：先验证静坐、实际离座与干扰，再验证坐姿变化和相邻座位，最后扩展到六人桌。

[首页](README.md) · [联调记录](对接事宜/README.md) · [成员与贡献](TEAM.md)
