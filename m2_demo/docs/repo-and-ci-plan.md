# 仓库初始化与 CI/CD 方案

日期：2026-10-21
适用：一个 monorepo，三名成员，三服务独立部署

---

## 1. 仓库结构

```text
/
├── AGENTS.md
├── README.md
├── .env.example
├── .gitignore
├── docs/
│   ├── adr/                     # 决策记录
│   ├── requirements/{m1,m2,m3}.md
│   ├── api/                     # 跨模块契约
│   ├── release/                 # 发布记录、回滚演练
│   └── discussion.md
├── backend/
│   ├── shared/                  # 仅：JWT 验签、日志、HTTP 客户端、错误码
│   └── services/{m1,m2,m3}/
│       ├── app/
│       ├── alembic/             # 各模块自己的迁移链
│       ├── tests/
│       ├── Dockerfile
│       └── requirements.txt
├── frontend/                    # Vue 3 + Vite + Element Plus
├── tests/integration/           # 跨模块集成测试
├── deploy/
│   ├── compose.yml
│   ├── compose.test.yml
│   ├── compose.prod.yml
│   └── migrate.sh
├── scripts/
│   ├── smoke.py
│   ├── deploy.sh
│   └── rollback.sh
└── .github/
    ├── CODEOWNERS
    ├── pull_request_template.md
    └── workflows/
        ├── ci.yml
        ├── deploy-test.yml
        ├── deploy-prod.yml
        └── rollback-prod.yml
```

---

## 2. 分支与提交

| 项 | 规则 |
|---|---|
| 常驻分支 | `main`，始终可部署 |
| 功能分支 | `feat/m2-03-approval`、`fix/m2-...`、`chore/ci-...` |
| 合并 | Squash merge，main 保持线性 |
| PR 标题 | `[M2-03] 两级审批状态机` |
| 评审 | 至少 1 人；跨模块契约 / 公共层 / schema 变更需 2 人 |
| 冲突 | 人工解决，AI 不得自动合并 |
| 提交信息 | Conventional Commits |
| main 保护 | 禁止直推、要求 CI 全绿、要求对话解决、要求 PR 评审 |

每个成员必须在第一周通过 PR 产生自己的初始提交，满足「独立提交记录」要求。

---

## 3. CI 流水线（PR 与 main）

```text
lint (ruff / eslint)
  → 单元测试 (pytest / vitest)
  → 空库迁移 (每个服务 alembic upgrade head + alembic check)
  → 集成测试 (Compose 起全栈，跑 tests/integration)
  → 前端构建
  → 构建并推送镜像（仅 main，GHCR，tag=commit sha）
```

质量门禁：

- 任一阶段失败，后续阶段不执行；
- PR 上 required checks 不绿禁止合并；
- 集成测试使用自己的临时 Compose，不与测试环境共用，避免部署窗口冲突；
- 测试报告（JUnit）作为 artifact 保留；
- 关键测试做一次「故意破坏实现」验证，证明测试能失败。

本原型的 `ci.yml` 已实现前三项 + Compose 冒烟。

---

## 4. 测试环境（main 自动部署）

- 触发：合并到 main 或手动。
- 部署：SSH 到测试服务器，`git checkout <sha>` → `docker compose up -d --build`。
- 入口脚本自动执行 `alembic upgrade head`。
- 健康检查 `/health` 与 `/version` 失败则回退上一版本并告警。
- 不需要登录服务器逐条执行命令。

## 5. 生产环境（tag + 人工审批）

- 触发：推送 `v*` tag。
- GitHub Environment `production` 配置 Required reviewers。
- 步骤：备份数据库 → 迁移 → 部署 → 健康检查 → 写发布记录。
- `/version` 返回 `git_sha` / `git_tag`，证明当前运行版本。
- 回滚：`rollback-prod.yml` 选择历史 tag，切换并重启；不重建镜像。
- 前提：迁移采用 expand/contract，删除列至少晚一个版本。

---

## 6. W1–W2 落地清单（B）

- [ ] 创建仓库目录、README、AGENTS.md、`.gitignore`、`.env.example`
- [ ] 五份 ADR 评审通过（服务拆分 / 数据库隔离 / 认证 / 迁移 / 发布）
- [ ] Compose 能一键启动 PostgreSQL + 三个服务占位
- [ ] Alembic 框架 + 空库迁移 + 迁移规范
- [ ] CI：lint + test + 空库迁移 + Compose 冒烟
- [ ] main 分支保护、PR 模板、CODEOWNERS
- [ ] GitHub Environments（test / production）、GHCR 权限
- [ ] M2 申请 / 审批数据表与接口
- [ ] 测试环境服务器与部署链路打通

---

## 7. 待确认

1. 测试/生产服务器归属与 SSH 方式（或 self-hosted runner）。
2. 镜像仓库使用 GHCR 还是其他。
3. 生产审批人名单。
4. 部署窗口：集成测试期间是否暂停测试环境自动部署。
5. 前端是否单独容器，还是由 Nginx 统一托管。
