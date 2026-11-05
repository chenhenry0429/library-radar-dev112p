# 谁做了什么 · 成果与待办看板

本页整理日期：2026-11-05。只把已经进入 main 的文件列为已提交成果，后续新增工作请同步更新本页和成员目录。

| 成员 / 模块 | 已有成果 | 证据 / 入口 | 仍未完成 |
|---|---|---|---|
| stone140 / A | 离线回放页面、内置演示、页面说明 | [A 工作区](A_stone140_应用与业务/README.md)、[原提交 f78133f](https://github.com/chenhenry0429/library-radar-dev112p/commit/f78133fd8e33616110529334ac6138850a04c397) | 实测联调、多座展示 |
| chenhenry0429 / B、维护 | 原始数组归档、严格校验、离线交付；仓库、手册和自动测试 | [B 工作区](B_chenhenry0429_雷达与采集/README.md)、[初始公共基线 cfec766](https://github.com/chenhenry0429/library-radar-dev112p/commit/cfec766892e6087c0ffad4ac9aae8d632514aa6c) | 到货连接、透传解码、真实采集 |
| cy071210 / C | 数据需求与流程/信号链文档；M1 合成输入原型和演示包 | [C 工作区](C_cy071210_识别算法/README.md)、[PR #2](https://github.com/chenhenry0429/library-radar-dev112p/pull/2)、[后续提交 8e723d7](https://github.com/chenhenry0429/library-radar-dev112p/commit/8e723d77045a17f110fcbf49b6e8d1e6f12b78a9) | 真实载荷适配、正式算法入口与实测评价 |
| stone140 / D | 已约定独立标签字段和评价边界 | [D 工作区](D_stone140_标注与评价/README.md) | 标注工具、标签校验与评价程序 |
| 全组 / shared | 文件契约、公共状态处理、回归测试和模拟业务流程 | [shared](shared/README.md)、[selftest.py](selftest.py)、[offline_state_machine.py](offline_state_machine.py) | 结合真实数据调整；跨座位协议升级 |

## 当前优先顺序

1. B 在到货前核实资料、连接链路和首次实验记录。
2. A 用公共输出核对回放；D 制作独立标注/评价工具。
3. C 先把真实输入适配与严格接口校验接上，再评价存在/姿态原型。
4. 真实单座闭环后，验证相邻两座，再扩展到六人桌和多桌。

## 如何维护看板

- 新成果写上实际文件、PR 或提交链接；开发分支中的未合入内容单独标注。
- 目录负责人表示职责，不改变已有 Git 作者署名。代码移动保留历史；查看文件时可使用 History 或 `git log --follow -- <文件路径>`。
- 不把文档规划、合成原型、设备实测和实际产品覆盖混写成同一个“完成”状态。
