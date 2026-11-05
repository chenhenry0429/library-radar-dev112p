# 图书馆座位占用与坐姿提醒 · DEV112P

面向面对面六人桌、高隔板自习位的小组项目。目标是逐座位检测占用、管理离座等待，并研究相对个人参考的前倾/后靠提示，逐步扩展至多个座位。

**当前可运行的是单座位离线接口 v1 和模拟联调流程。** 仓库包含 B 的文件归档/校验、共享接口及 A 的离线状态处理；真实 DEV112P 透传解码、人体存在与姿态分类模型尚未接入。`DEMO_ONLY` 全部为 `synthetic`，不能用于识别准确率评价。

## 先运行一次

建议使用 Python 3.12。锁定依赖为 NumPy 2.3.5，需 Python 3.11+。在仓库根目录打开终端：

```powershell
python -m pip install -r requirements.txt
python selftest.py
python radar_offline.py demo --output DEMO_ONLY/my_first_demo
```

Windows 也可在准备好环境后双击 `run_demo.cmd`，它会为本次演示新建目录并生成离线交付 ZIP。自动启动器保留本机已有环境兼容方式；不自动安装依赖。

结果在本次演示目录中的 `output/<recording_id>/demo_run_01/`：

- `timeline.csv`：逐帧占用、姿态、计时和复核状态。
- `events.csv`：入座、离座等待、释放、提醒及数据缺口。
- `run.json`：本次参数、输入校验值、会话完成情况。

同名输出不会覆盖，请为下一次运行使用新的目录或 `run-id`。

## 小组模块与交接

```text
B 已解码采集 -> raw.npz + meta.json
                         |
                         v
C 因果算法  -> result.csv + algorithm.json
                         |
                         v
A 离线业务  -> timeline.csv + events.csv + run.json

D 独立标注  -> labels.csv -> 评价（不参与 C 推理或 A 状态处理）
```

| 模块 | 工作入口 | 当前状态 |
|---|---|---|
| A 应用与业务 | [modules/a_application](modules/a_application/README.md) | 离线状态处理已实现；网页展示待开发 |
| B 雷达与采集 | [modules/b_acquisition](modules/b_acquisition/README.md) | 已解码数据归档/校验/交付已实现；硬件协议解析待接入 |
| C 识别算法 | [modules/c_algorithm](modules/c_algorithm/README.md) | 接口和模拟证据已提供；真实分类器待开发 |
| D 标注与评价 | [modules/d_evaluation](modules/d_evaluation/README.md) | 标注/评价方案已约定；评价程序待开发 |
| 共享接口 | [shared](shared/README.md) | 单一字段定义与校验，由跨模块共同维护 |

当前可执行文件集中保留在根目录，以兼容已交付的离线工具与手册；模块目录用于任务入口和职责边界，不复制四套代码。模块之间通过约定的文件交接，不导入 D 的真值来做推理。

## 参与项目 / Contributors

| 成员 | 负责模块 |
|---|---|
| [@chenhenry0429](https://github.com/chenhenry0429) | B：雷达与采集；仓库维护 |
| [@stone140](https://github.com/stone140) | A：应用与业务；D：独立标注与评价 |
| [@cy071210](https://github.com/cy071210) | C：识别算法 |

小组成员接受仓库协作者邀请后，可创建分支并提交 Pull Request。其他参与者也可通过 Fork + Pull Request 贡献。加入步骤、成员职责和首次提交方式见 [项目成员](TEAM.md) 与 [协作指南](CONTRIBUTING.md)。

GitHub 侧栏的 **Contributors** 根据实际提交自动统计；成员表用于说明分工，不会直接改变统计或授予权限。提交需要进入默认分支 `main`，且作者邮箱关联本人的 GitHub 账号。查看 [实际贡献记录](https://github.com/chenhenry0429/library-radar-dev112p/graphs/contributors)。

## 文档

- [7 页操作手册 PDF](docs/operation-manual.pdf)
- [详细工具说明](README_先读我.md)
- [离线接收说明](README_组员离线读取.txt)
- [架构与模块规划](docs/architecture.md)
- [离线字段与版本约定](shared/README.md)
- [验证记录](docs/validation-report.md)
- [小组协作方法](CONTRIBUTING.md)

## 当前验收与后续目标

本地已有 28 项回归测试，覆盖字段/帧时间对应、占用确认、姿态计时、标定、未知与数据缺口、EOF、确定性及离线 ZIP 校验。GitHub 工作流会在 Windows / Linux 的 Python 3.12 环境再次执行测试；远程结果以 Actions 为准。

后续顺序：取得真实协议并完成单座采集闭环 -> C 实测存在/姿态算法 -> D 独立评价 -> A 展示 -> 相邻两座并发验证 -> 高隔板六人桌与多桌扩展。多座位目标不等于 v1 已支持多人分离或多节点并发。

## 数据与许可证

真实采集、人员标注、设备序列号、订单、授权文件和厂商工具不随源代码发布。仓库只带可复现的模拟例子；模拟运行记录的本机路径已改为相对路径。使用 `recordings/` 等被忽略目录保存本地真实数据。

团队尚未选定开源许可证，暂不自动添加 LICENSE；后续由团队统一确认。
