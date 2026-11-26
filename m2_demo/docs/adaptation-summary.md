# M2 适配成员 A（M1）工作总结

日期：2026-10-21
负责人：张海容（成员 B）
目标：让 M2 在**不改内部模型**的前提下，直接对接成员 A 的 M1 实现（PR #1），并保留原有演示与测试能力。

---

## 1. 结论

- M2 已可直接对接 A 的真实 M1：读取教室、两级审批、写回整教室限制、M1 侧冲突拦截、撤销释放，全流程跑通。
- 同时保留 `m1_mock`，其接口已镜像 A 的 PR #1，Compose 与 CI 不依赖 A 的代码。
- 本地验证：`ruff` 通过、`pytest` **25 passed**、空库 Alembic 迁移通过、Compose 冒烟通过。
- **尚未提交或推送任何代码**；远端仓库当前无 M2 分支/PR。

---

## 2. 适配策略

采用**防腐层（anti-corruption layer）**：只在 `M1Client` 边界做转换，M2 的数据库、状态机、接口保持不变。

| 差异点 | M2 内部 | A 的 M1 | 适配方式 |
|---|---|---|---|
| 时间 | UTC naive | 北京时间 naive 字符串 | 客户端出口 UTC→北京，入口北京→UTC |
| 教室 ID | 字符串 | 整数 | 出口转 `int`，入口转 `str` |
| 整教室占用 | `sync_records` outbox | `POST /api/integrations/restrictions` | `application_id` 作为 `external_id` 幂等键 |
| 冲突预检 | 查 M2 数据库 | `GET /api/seats` | 二级审批前只读预检 |
| 认证 | M2 JWT | `X-Demo-User` | 固定 `admin`，配置项可改 |

这样 A 后续修改接口时，只需改适配层，不影响 M2 业务代码与测试。

---

## 3. 代码改动

### 3.1 M2 服务

| 文件 | 改动 |
|---|---|
| `backend/services/m2/app/m1_client.py` | 重写：时间/ID 转换、A 的四个接口、错误可重试分类、`trust_env=False`、`/admin/state` 降级 |
| `app/config.py` | 新增 `M2_M1_DEMO_USER` |
| `app/main.py` | 注入 `demo_user` |
| `app/schemas.py` | `RoomOut` 对齐 A 的 `id/name/area/opens/closes/active`；新增 `M1PersonalBookingIn` |
| `app/routers/applications.py` | 二级审批前调用 M1 冲突预检；M1 临时不可用不阻塞审批 |
| `app/worker.py` | 永久失败（4xx / `active=0`）直接置 `DEAD`，不再无意义重试 |
| `app/routers/ops.py` | 新增 `POST /ops/m1-personal-booking`；M1 状态兼容降级 |
| `web/index.html` | 教室展示、M1 限制列表、制造个人预约按钮、真实 M1 下隐藏故障注入 |
| `tests/conftest.py` | `FakeM1` 对齐 A 的接口语义 |
| `tests/test_m2_flow.py` | 教室 ID 改为整数；新增 M1 冲突、永久失败、M1 不可用三个用例 |
| `tests/test_m1_client.py` | **新增 12 个适配层用例**（时间、ID、请求体、错误分类、释放路径） |

### 3.2 M1 模拟服务

`backend/services/m1_mock/app/main.py` 按 A 的 PR #1 重写：

- 3 间教室、每间 24 座、全局座位 ID
- 北京时间校验（拒绝带时区后缀）
- `/api/meta`、`/api/seats`、`/api/reservations`
- restrictions 的幂等、参数变更 409、个人预约冲突 409
- `/admin/*` 故障注入，仅演示用

### 3.3 工程与文档

| 文件 | 改动 |
|---|---|
| `deploy/compose.yml` | 增加 `M2_M1_DEMO_USER: admin` |
| `scripts/smoke.py` | 适配新接口；增加撤销释放断言 |
| `docs/api/m1-m2-contract.md` | 升级 v0.2，按 A 的实际实现重写 |
| `README.md` | 新增适配说明、真实 M1 接法、测试数更新 |
| `docs/discussion.md` | 更新待 A 确认事项 |

无数据库结构变更，因此**没有新增 Alembic 迁移**；空库迁移与 `alembic check` 均通过。

---

## 4. 关键设计

### 4.1 错误分类与 Outbox

| M1 返回 | 处理 |
|---|---|
| 2xx | `SUCCEEDED` |
| 网络错误 / 5xx | `FAILED`，2/4/8/16/32 秒退避重试 |
| 4xx | 永久失败，直接 `DEAD` |
| 2xx 但 `active=0` | 视为静默失败，直接 `DEAD` |

审批结果与同步结果始终分开展示，不谎报「已占用」。

### 4.2 二级审批 M1 预检

1. 锁 M2 教室行，检查 M2 自身冲突；
2. 调 `GET /api/seats` 查 `sources`；
3. 命中 `M1:个人预约` 或 `M2:*` / `M3:*` → 409，申请保持 `PENDING_L2`；
4. M1 临时不可用 → 记录告警，继续审批，由 outbox 重试兜底；
5. 预检与写回之间的竞态，由 outbox 的 409 永久失败兜底。

### 4.3 对 A 的兼容降级

A 没有 `/admin/state` 和故障注入接口。M2 先尝试 `/admin/state`，404 时改读 `/api/admin/overview`，返回 `demo_controls: false`；运维页自动隐藏故障注入/重置按钮，但保留只读限制列表和「制造个人预约」。

---

## 5. 验证证据

### 5.1 本地自动化

| 检查 | 命令 | 结果 |
|---|---|---|
| Lint | `ruff check .` | 通过 |
| 单测 | `pytest` | 25 passed |
| 空库迁移 | `alembic upgrade head` | 0001_init 成功 |
| 迁移一致性 | `alembic check` | No new upgrade operations |
| Compose 冒烟 | `python3 scripts/smoke.py` | 全部通过 |

冒烟覆盖：登录 → 读 M1 教室 → 提交 → 一级/二级审批 → outbox 写回 → 校验 M1 限制 → 撤销 → 释放校验。

### 5.2 对接 A 的真实 M1（2026-10-21 验证）

| 场景 | 结果 |
|---|---|
| M2 读取 A 教室主数据 | 通过 |
| 两级审批 → outbox 写回 restriction | 通过 |
| 运维页在 A 无 `/admin/state` 时降级 | 通过（`demo_controls: false`） |
| A 侧个人预约占用 → M2 二级审批 | `409 M1 侧冲突：明德楼 202 的座位 01 已被 M1:个人预约 占用`，状态保持 `PENDING_L2` |
| 撤销已批准申请 → 释放 restriction | `active=0` |
| 总结果 | `INTEGRATION OK` |

---

## 6. 发现的问题（需 A 处理）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | 已释放的 `(source, external_id)` 再次 POST 返回 `200 + active=0`，实际未占用 | M2 会误判同步成功，造成静默不一致 | 返回 409，或显式重新激活后返回 200 |
| 2 | 只接受无时区北京时间字符串，带 offset 直接 422 | 跨服务时间语义脆弱 | 接受带 offset 的 ISO 8601，内部统一转换 |
| 3 | 服务间认证使用 `X-Demo-User: admin` | 正式环境不可用 | 接入 C 的服务 token / JWT |
| 4 | 同一座位同时存在 M1/M2/M3 限制时的恢复语义未明确 | 释放可能误恢复 | 明确「按来源解除、其他来源继续生效」并补测试 |
| 5 | 失败必须返回 4xx/5xx | 2xx 静默失败难排查 | 统一错误响应 |

M2 侧已对 #1 做防御：识别 `active=0` 后按永久失败处理，不再重试、不显示成功。

---

## 7. 待办与下一步

### 本人（B）

- [ ] 与 A 确认契约 v0.2 及上述 5 个问题
- [ ] 决定代码交付方式：fork + PR，还是等仓库权限
- [ ] 在 CI 中保留 Compose 冒烟；确认远端 CI 实际跑通（目前只本地验证）
- [ ] 演示前重置数据：`docker compose down -v && up -d --build`

### 依赖他人

- A：修正释放后复用语义、时间格式、服务认证
- C：JWT 字段/角色命名、通知与审计接口、公共前端接入方式

---

## 8. 运行方式

```bash
cd deploy && docker compose up -d --build
# 演示页 http://localhost:8080/demo/
# Swagger  http://localhost:8080/docs
# M1 mock  http://localhost:8081/docs
python3 scripts/smoke.py
```

对接 A 的真实 M1：

```bash
cd backend/services/m2
M2_DATABASE_URL=sqlite:///./m2.db \
M2_M1_BASE_URL=http://127.0.0.1:8000 \
M2_AUTO_CREATE_TABLES=true \
../../../.venv/bin/uvicorn app.main:app --port 8080
```
