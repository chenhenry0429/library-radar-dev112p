# 目录指南：按负责人找成果

整理日期：2026-11-05。四个顶层工作区用于直接展示负责人、已有成果和下一步；共享代码只保留一个实现。

| 工作区 | 负责人 | 打开后看什么 |
|---|---|---|
| [A_stone140_应用与业务](../A_stone140_应用与业务/README.md) | stone140 | A 的回放页面、页面说明、公共状态处理入口与联调任务 |
| [B_chenhenry0429_雷达与采集](../B_chenhenry0429_雷达与采集/README.md) | chenhenry0429 | 采集/交付工具索引、到货前后任务和验收 |
| [C_cy071210_识别算法](../C_cy071210_识别算法/README.md) | cy071210 | 数据需求、处理流程、信号链说明与 M1 合成输入脚本 |
| [D_stone140_标注与评价](../D_stone140_标注与评价/README.md) | stone140 | 独立标签、数据划分和评价程序待办 |

总览见 [WORK_STATUS.md](../WORK_STATUS.md)。已进入主分支、开发中和待设备验证分别标注，不将负责人目录等同于每一行代码的实际作者。

## 旧路径对应

| 旧路径 | 新的唯一实现/工作入口 |
|---|---|
| modules/a_application/ | A_stone140_应用与业务/ |
| modules/a_application/index.html | A_stone140_应用与业务/offline_viewer/index.html |
| modules/a_application/offline_viewer/index.html（旧说明中曾引用） | A_stone140_应用与业务/offline_viewer/index.html |
| modules/b_acquisition/ | B_chenhenry0429_雷达与采集/ |
| modules/c_algorithm/ | C_cy071210_识别算法/ |
| modules/c_algorithm/docs/generate_demo.py | C_cy071210_识别算法/generate_demo.py |
| modules/c_algorithm/docs/下的设计文档 | C_cy071210_识别算法/docs/ |
| modules/d_evaluation/ | D_stone140_标注与评价/ |

modules 下保留说明跳转、旧网页跳转和 C 演示脚本的命令转发，避免已有链接/命令失效；不继续在这里增加独立功能或维护第二份网页数据。下载仓库时应保留整个目录结构。

## 根目录为什么还有程序

`radar_offline.py`、`offline_state_machine.py`、`contract.py`、读取/校验脚本、配置模板、Windows 启动器和 selftest.py 是已交付的公共运行入口。将它们继续留在根目录可以保留原有命令、离线交付包及 CI，不为每个负责人复制一份。

docs 存放公共手册与架构；DEMO_ONLY 存放标明 synthetic 的公共样例。C 演示脚本运行产生的 outputs 目录被 Git 忽略，真实采集仍单独保存在本地 recordings 等目录。

DEMO_ONLY 中随历史演示包保存的生成脚本属于该次交付快照，保留用于复现；后续 C 开发以成员工作区中的脚本为维护入口。

## 开发分支怎样跟上新目录

组员先同步 main，再参照本表把自己的新增文件放到对应工作区。若旧分支继续在 modules/c_algorithm 等位置新增文件，合并时需要人工确定其新归属，不能盲目覆盖兼容入口。本次没有额外合并或改写任何开发分支。

移动已有文件使用独立的纯重命名提交；原内容与提交作者历史保留，后续导航文档更新另作提交。

## 本次整理检查

2026-11-05：28 项公共回归测试通过；成员目录、Markdown 本地链接、网页资源与旧入口跳转已检查。C 的新路径与旧命令转发均能运行，生成的四个文件内容一致。这些检查验证目录迁移的兼容性，不代替 C 原型的真实数据识别评价。
