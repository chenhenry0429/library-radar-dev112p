# 小组协作

## 加入仓库

仓库地址：https://github.com/chenhenry0429/library-radar-dev112p 。项目分工见 [TEAM.md](TEAM.md)。

小组成员由仓库拥有者使用 GitHub 用户名邀请，接受后成为 **Collaborator（协作者）**，可以向本仓库推送代码并创建分支。个人账号仓库的协作者权限适用于整个仓库，模块分工不限制目录访问。

1. 拥有者在仓库 **Settings → Collaborators → Add people** 中选择组员账号并发送邀请；入口为 [协作者设置](https://github.com/chenhenry0429/library-radar-dev112p/settings/access)。
2. 组员登录被邀请的 GitHub 账号，通过 GitHub 邀请通知接受，或打开 [邀请页面](https://github.com/chenhenry0429/library-radar-dev112p/invitations)。发送邀请与接受邀请是两个步骤。
3. 接受后克隆仓库，创建自己的功能分支并提交 Pull Request。没有写入权限的参与者也可 Fork 后提交 Pull Request。

## 让真实贡献计入 Contributors

**Contributors（贡献者统计）** 与协作者权限分开：接受邀请、被写入成员表或仅参与讨论，不会直接产生提交贡献。GitHub 根据默认分支的提交计算贡献；合并提交和空提交不计入此图表。

- 用自己的 GitHub 账号创作和提交实际改动，例如补充自己模块的任务说明、修正操作文档，或完成对应功能。
- 本机 Git 的作者邮箱需关联本人 GitHub 账号，也可以使用本人 GitHub 邮箱设置中提供的 `noreply` 地址。不要使用其他成员的邮箱。
- 将改动通过 Pull Request 合并到 `main`，保留实际作者署名；多人共同完成时按真实参与情况记录共同作者。
- 如果没有显示，先检查提交是否已进入 `main`、作者邮箱是否正确，再查看 [Contributors 图表](https://github.com/chenhenry0429/library-radar-dev112p/graphs/contributors)。不通过空提交或伪造他人署名增加统计。

## 开发流程

1. 从 main 拉取最新版本；围绕一个任务创建分支，例如 feat/b-decoder、feat/c-presence、feat/a-viewer、feat/d-evaluation。
2. 各模块入口见 modules/；共享字段以根目录 contract.py 为唯一实现。调整字段、时间、枚举或文件命名时先在 PR 中说明跨模块影响。
3. 用合成夹具补充需要验证的边界，运行 python selftest.py。真实数据只保存在本地被忽略目录，不用公开采集样本替代可复现测试。
4. PR 描述写清触发情形、输出变化、验证方法和仍未实现的功能；不要将模拟分类证据描述为真实识别算法。
5. 合并前由相关上下游组员检查接口；先保证 B -> C -> A 文件可交接，D 的标注继续保持独立。

共享接口变更需要相关上下游成员共同检查。当前模块分工用于协作安排，尚未配置强制代码审查规则或目录权限隔离。

## 依赖与运行

公开仓库以 Python 3.12 / requirements.txt 为复现基线。原有工具文档中的 Python 3.10+ 指代码接口下限，不代表锁定 NumPy 2.3.5 可在 3.10 安装；本仓库锁定依赖需 Python 3.11+。

CI 使用 GitHub 官方 [checkout](https://github.com/actions/checkout) 与 [setup-python](https://github.com/actions/setup-python)，仅授予内容读取权限。CI 不自动发布真实数据、不运行雷达、不播放提醒声音。

## GitHub 官方参考

- [邀请个人仓库协作者](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)
- [个人仓库的权限](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository)
- [贡献者统计与显示条件](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors)
