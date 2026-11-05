# 图书馆座位占用与坐姿提醒 · DEV112P

面向面对面六人桌、高隔板自习位的小组项目，逐步验证多座位占用管理和个人参考下的姿态变化提示。

**当前阶段：传感器待到货；已有单座离线工具、回放页面和合成数据原型。** 仓库中的演示数据为 `synthetic`，不能作为真实雷达识别效果。

## 谁负责什么，已经做了什么

点击带名字的目录，直接查看该成员的成果、运行入口和下一步任务。

| 负责人 | 工作目录 | 已进入 main 的成果 | 下一步 |
|---|---|---|---|
| [stone140](https://github.com/stone140) · A | [A_stone140_应用与业务](A_stone140_应用与业务/README.md) | 离线回放网页、内置演示和使用说明；可使用已有公共状态处理工具 | 前后端联调和边界场景核对 |
| [chenhenry0429](https://github.com/chenhenry0429) · B | [B_chenhenry0429_雷达与采集](B_chenhenry0429_雷达与采集/README.md) | 已解码数组归档、文件校验、离线打包和读取工具 | 设备到货验收、真实协议与采集接入 |
| [cy071210](https://github.com/cy071210) · C | [C_cy071210_识别算法](C_cy071210_识别算法/README.md) | 数据需求、处理流程/信号链文档、M1 合成输入演示脚本及样例 | 真实载荷适配和独立数据验证 |
| [stone140](https://github.com/stone140) · D | [D_stone140_标注与评价](D_stone140_标注与评价/README.md) | 已约定独立标签格式和评价任务；评价程序尚未提交 | 标注细则、标签校验和评价脚本 |

[查看成果与待办看板](WORK_STATUS.md) · [查看成员与加入方式](TEAM.md) · [查看实际提交贡献](https://github.com/chenhenry0429/library-radar-dev112p/graphs/contributors)

## 目录导航

```text
A_stone140_应用与业务/
  README.md                       A 的成果与任务
  offline_viewer/                  回放网页、内置数据、使用说明
B_chenhenry0429_雷达与采集/
  README.md                       B 的工具索引、到货前后任务
C_cy071210_识别算法/
  README.md                       C 的成果、运行与验证边界
  generate_demo.py                M1 合成数据演示脚本
  docs/                           数据需求、B0/M1、信号链说明
D_stone140_标注与评价/
  README.md                       独立标注和评价任务
shared/                           共享接口入口，字段定义见 contract.py
docs/                             操作手册、验证与架构文档
DEMO_ONLY/                        公共模拟数据，按采集编号存放
modules/                          旧路径兼容入口
radar_offline.py                  全组统一命令入口
offline_state_machine.py          公共 A 状态处理基线
contract.py                      公共字段与严格校验
selftest.py                      公共回归测试
```

负责人目录表示模块职责；文件的实际作者和变更历史仍以 Git 提交为准。根目录 Python/Windows 启动入口继续可用，不为每个成员复制一套公共工具。旧路径对应关系见 [目录迁移说明](docs/directory-guide.md)。

## 先运行一次

建议 Python 3.12，锁定 NumPy 2.3.5（需 Python 3.11+）。在仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
python selftest.py
python radar_offline.py demo --output DEMO_ONLY/my_first_demo
```

Windows 可在准备好环境后双击 `run_demo.cmd`。本次运行输出位于演示目录内的 `output/<recording_id>/demo_run_01/`，包含 `timeline.csv`、`events.csv` 和 `run.json`；同名输出不会覆盖。

查看回放：下载并完整解压仓库，双击 [A 的 offline_viewer/index.html](A_stone140_应用与业务/offline_viewer/index.html)，点击“载入模拟演示”，或选择同一次 A 运行的三个输出文件。GitHub 文件预览页显示源代码，需下载后打开网页。

## 组间怎么交接

```text
B 已解码采集 -> raw.npz + meta.json
                         |
                         v
C 因果算法  -> result.csv + algorithm.json
                         |
                         v
A 离线业务  -> timeline.csv + events.csv + run.json

D 独立标注  -> labels.csv -> 评价（不作为 C 推理或 A 状态处理输入）
```

共享字段由 [contract.py](contract.py) 和 [shared](shared/README.md) 统一维护。v1 每目录只处理一个座位、一段连续采集；真实多座并发和硬件识别仍需验证。

## 操作说明与协作

- [7 页操作手册 PDF](docs/operation-manual.pdf)
- [详细工具说明](README_先读我.md) · [离线接收说明](README_组员离线读取.txt)
- [架构与阶段规划](docs/architecture.md) · [原有验证记录](docs/validation-report.md)
- [如何加入、建分支与提交 PR](CONTRIBUTING.md)

小组成员接受协作者邀请后可创建分支；其他参与者可 Fork 后提交 Pull Request。成员分工与 GitHub Contributors 统计分开，署名和权限说明见 [TEAM.md](TEAM.md)。

真实采集、人员标注、设备序列号、订单、授权文件和厂商工具不随源代码发布。模拟示例保留来源标记。团队尚未选定开源许可证，暂不自动添加 LICENSE。
