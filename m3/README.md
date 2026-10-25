# M3 物业报修与公共服务

成员 C 的独立模块：报修、派单、维修、超时升级、验收评价，以及登录/RBAC、站内通知、操作审计和工作台页面。通过 M1 公开 HTTP 接口下架/恢复教室座位，支持持久化任务、失败重试和来源隔离，不修改 M1 原工程。

## 本地运行

在仓库根目录使用 Python 3.11+：

```powershell
.\.venv\Scripts\python.exe -m pip install -r m3\requirements-dev.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.\.venv\Scripts\python.exe -m m3.manage migrate
.\.venv\Scripts\python.exe -m m3.manage create-user --username admin --name 管理员 --role admin
.\.venv\Scripts\python.exe -m uvicorn m3.app:create_app --factory --host 127.0.0.1 --port 8001
```

创建账号时交互设置至少 12 位密码，无默认密码。管理员登录后可创建学生、维修人员和审批人账号。页面位于 `http://127.0.0.1:8001`，API 文档为 `/docs`。

M1 在另一终端按根 README 启动，默认地址 `http://127.0.0.1:8000`。完整报修演示需要两个服务同时可用。M3 内部调用不继承环境代理，避免本地访问误入代理产生 502。

配置清单见 [.env.example](.env.example)，通过环境变量设置，应用不自动读取 `.env`。默认数据库 `m3/data/m3.sqlite3` 独立于 M1；该目录和真实 `.env` 不纳入版本管理。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests tests_m3 -q
.\.venv\Scripts\python.exe -m m3.check_mutations skip-release
.\.venv\Scripts\python.exe -m m3.check_mutations bypass-ownership
```

本地最近一次回归为 55 项通过、2 项跳过。浏览器与真实网络测试需显式启用：`M3_BROWSER_TEST=1`、`M3_NETWORK_ALLOW_WRITE=1`，所需配置见各测试文件。普通回归使用临时数据库，不要求启动服务。故障注入命令仅在独立进程内替换函数，以关键测试失败为预期结果。

## 当前边界

- M1 当前仍使用演示鉴权；统一身份映射和正式服务鉴权需 A/C 联调。
- RBAC 目前为预置四角色，自定义角色/权限与幂等 seed 尚待补齐。
- 真实网络闭环、团队人工 Review、整组 CI/CD 与部署回滚尚待验收；不能将本地测试等同于课程最终交付。
- 本次推送不包含工作区 `docs/` 中的进度、报告与截图，M1 原有已提交文档保持不变。
