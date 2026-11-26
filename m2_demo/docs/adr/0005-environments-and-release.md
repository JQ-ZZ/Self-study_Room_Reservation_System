# ADR-0005：环境、发布与回滚策略

- 状态：提议
- 日期：2026-10-21
- 决策者：A / B / C（待确认）

## 背景

任务书要求：测试环境自动部署、生产发布需人工审批、部署版本可追溯、能演示回滚，
且组内各模块的发布顺序需要协调。

## 决策

1. 三个环境：本地（Compose）、测试（main 合并自动部署）、生产（tag + 人工审批）。
2. 镜像推送到 GHCR，标签为 commit SHA；发布 tag 形如 `v0.5.0`，记录 tag ↔ commit。
3. 测试环境由 `deploy-test.yml` 自动 SSH 部署，健康检查失败自动回退上一镜像。
4. 生产环境绑定 GitHub Environment `production`，配置 Required reviewers；部署前备份数据库。
5. 回滚：`scripts/rollback.sh <tag>` 只切换镜像标签并重启，不重建、不执行降级迁移。
6. 同一时刻只允许一个部署任务（`concurrency`），避免组内互相覆盖。
7. 每个服务暴露 `/version`，返回 `git_sha` / `git_tag`，现场可证明当前运行版本。

## 理由

- 课程项目一台服务器足够，不需要 Kubernetes。
- 镜像 tag + 健康检查 + Environment 审批可以用 GitHub 原生能力完成全部硬性要求。

## 待确认

- 服务器归属与 SSH 访问方式。
- 是否使用 self-hosted runner。
- 生产数据库备份位置与保留策略。
- 部署窗口：集成测试期间是否暂停测试环境部署。
