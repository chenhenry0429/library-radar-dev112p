# 小组协作

1. 从 main 拉取最新版本；围绕一个任务创建分支，例如 feat/b-decoder、feat/c-presence、feat/a-viewer、feat/d-evaluation。
2. 各模块入口见 modules/；共享字段以根目录 contract.py 为唯一实现。调整字段、时间、枚举或文件命名时先在 PR 中说明跨模块影响。
3. 用合成夹具补充需要验证的边界，运行 python selftest.py。真实数据只保存在本地被忽略目录，不用公开采集样本替代可复现测试。
4. PR 描述写清触发情形、输出变化、验证方法和仍未实现的功能；不要将模拟分类证据描述为真实识别算法。
5. 合并前由相关上下游组员检查接口；先保证 B -> C -> A 文件可交接，D 的标注继续保持独立。

暂不在 CODEOWNERS 中填写未知账号，也不自动邀请协作者。仓库管理员得到组员 GitHub 用户名后，在仓库设置中添加协作权限。

## 依赖与运行

公开仓库以 Python 3.12 / requirements.txt 为复现基线。原有工具文档中的 Python 3.10+ 指代码接口下限，不代表锁定 NumPy 2.3.5 可在 3.10 安装；本仓库锁定依赖需 Python 3.11+。

CI 使用 GitHub 官方 [checkout](https://github.com/actions/checkout) 与 [setup-python](https://github.com/actions/setup-python)，仅授予内容读取权限。CI 不自动发布真实数据、不运行雷达、不播放提醒声音。
