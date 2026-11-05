# C · 识别算法

**当前状态**：只包含共享契约与 synthetic 演示证据，尚无真实存在/姿态分类器。请不要把 synthetic_algorithm 或 evidence 函数作为已训练模型。

输入：B 的 raw.npz + meta.json；先核实数据级别与阵列/轴/单位，不预设 raw 是二维、ADC、点云或距离谱。

输出：result.csv + algorithm.json，保持原始 N 帧到 N 行逐行对应；无效、预热、拒识也留行，未知字段按约定填写。

下一项任务：用真实空场/静坐样本先完成因果存在证据；之后比较明确指定个人参考下的 reference/forward/backward。没有姿态功能时声明 capabilities.posture=false，仍输出九列，姿态为 unknown/not_applicable、标定 uncalibrated。

验收：validate --stage c 通过，字段与能力一致；无未来帧或测试标签参与推理；报告窗口、平滑、模型/参数版本及参考起止、生效、失效。A 承担时间累计与提醒，C 不重复加入业务 10 秒提醒或 60 秒释放。
