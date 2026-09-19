# 仓库协作约定

- 每轮开始修改前，先执行 `git fetch origin main`，再将最新的 `origin/main` 通过 `git merge --no-edit origin/main` 合并到当前工作分支。不要只检查分支状态或使用未经更新的本地 main。
- 同步前检查工作区，妥善保留未提交改动及其他工作树；不要用 reset 或覆盖文件的方式完成同步。处理合并冲突后，按实际影响验证。
