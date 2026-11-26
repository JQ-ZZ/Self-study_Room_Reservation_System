# M2 整教室预约与审批 · 演示原型

软件过程管理课程 PJ 的 M2 模块可运行原型，用于组内讨论与需求评审。
覆盖任务书中的 M2 核心闭环，并把「跨模块一致性」问题做成了可演示的失败/重试场景。

> 负责人：张海容（成员 B）
> 职责：M2 业务 + 数据库迁移体系、Docker、CI/CD、部署与回滚

---

## 1. 已实现的功能

| 编号 | 功能 | 演示方式 |
|---|---|---|
| M2-01 | 提交教室预约申请 | 申请人填写教室、时间、用途并提交 |
| M2-02 | 权限门槛 | 学生不能审批；一级审批人不能处理二级；接口返回 403 |
| M2-03 | 两级审批 | `待一级 → 待二级 → 已批准`，逐级记录审批人和意见；同一人不能完成两级 |
| M2-04 | 冲突检查 | 同一教室重叠时段的第二份申请，在二级审批时返回 409，状态不变 |
| M2-05 | 超时撤销 | 后台定时任务扫描 `expires_at`，自动置为 `EXPIRED`（演示时限 90 秒） |
| M2-06 | 查询与取消 | 我的申请、审批轨迹、待审批列表；待审批可撤销，已批准且未开始可撤销并释放 |
| M2-07 | M2 → M1 状态写回 | 审批通过后通过 HTTP 写入 `M2:<application_id>` 整教室限制；驳回/撤销不产生限制，撤销已批准申请会释放 |
| M2-07a | 失败重试 | M1 故障时申请仍为已批准，但同步记录明确失败（不谎报成功），后台退避重试；4xx 永久失败直接置 DEAD |
| M2-07b | A 的 M1 适配 | 已按成员 A 的 PR #1 对齐：`/api/meta`、`/api/seats`、`/api/integrations/restrictions`，北京时间与整数教室 ID 在适配层转换 |

工程部分：

- **数据库迁移**：Alembic，空库 `upgrade head` 可一次迁移到最新
- **容器化**：Docker Compose 一键启动 PostgreSQL + M2 + M1 模拟服务
- **测试**：pytest 25 个用例，覆盖两级审批、权限、冲突、超时、M1 适配、同步失败重试、撤销释放
- **CI**：GitHub Actions（lint → 测试 → 空库迁移 → Compose 冒烟）
- **版本可见**：`/version` 返回 `git_sha` / `git_tag`，为发布与回滚演示预留

---

## 2. 一键启动

前置：Docker + Docker Compose。

```bash
cd m2-demo/deploy
docker compose up -d --build

# 等待三个容器 healthy
docker compose ps

# 端到端冒烟（可选）
cd ..
python3 scripts/smoke.py
```

打开：

- 演示页面：<http://localhost:8080/demo/>
- Swagger：<http://localhost:8080/docs>
- M1 模拟服务：<http://localhost:8081/docs>
- PostgreSQL：`localhost:15432`（app/app，库 `m2_db`）

停止并清空数据：

```bash
cd deploy && docker compose down -v
```

### 本地不用 Docker 启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/services/m2/requirements-dev.txt -r backend/services/m1_mock/requirements.txt

# 终端 1：M1 mock
(cd backend/services/m1_mock && ../../../.venv/bin/uvicorn app.main:app --port 8081)

# 终端 2：M2（SQLite，自动建表）
(cd backend/services/m2 && \
  M2_DATABASE_URL=sqlite:///./m2.db M2_M1_BASE_URL=http://localhost:8081 \
  M2_AUTO_CREATE_TABLES=true ../../../.venv/bin/uvicorn app.main:app --port 8080)
```

> 生产/测试环境不使用 `M2_AUTO_CREATE_TABLES`，由容器入口执行 `alembic upgrade head`。

### 直接对接成员 A 的真实 M1

A 的实现目前在 PR #1 的 fork `xiayongqi28-web/Self-study_Room_Reservation_System:M1`。拿到代码后：

```bash
# 终端 1：A 的 M1（默认 8000，SQLite）
cd <A的仓库目录>
python -m uvicorn m1_demo.app:app --host 127.0.0.1 --port 8000

# 终端 2：M2 指向 A，而不是 m1_mock
cd backend/services/m2
M2_DATABASE_URL=sqlite:///./m2.db \
M2_M1_BASE_URL=http://127.0.0.1:8000 \
M2_AUTO_CREATE_TABLES=true \
../../../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
```

M2 适配层会自动完成 UTC→北京时间、字符串→整数教室 ID、`X-Demo-User: admin` 等转换。
A 的 M1 没有 `/admin/state` 和故障注入接口，M2 会自动降级为只读总览，运维页隐藏故障注入/重置按钮；
「在 M1 制造个人预约」仍可用，用于演示二级审批的 M1 侧冲突拦截。

Docker 场景下把 `M2_M1_BASE_URL` 指向 `host.docker.internal:8000`（Linux 需给 m2 服务加
`extra_hosts: ["host.docker.internal:host-gateway"]`），或直接在本机跑 M2。

---

## 3. 演示账号

演示登录不校验密码，正式环境由成员 C 的认证服务签发 JWT，M2 只负责验签和接口级 RBAC 校验。

| 账号 | 姓名 | 角色 | 用途 |
|---|---|---|---|
| `student1` | 李同学 | student | 提交申请、撤销 |
| `student2` | 王同学 | student | 演示时段冲突 |
| `teacher1` | 陈老师 | teacher | 提交申请 |
| `approver1` | 赵主任 | approver_l1 | 一级审批 |
| `approver2` | 钱处长 | approver_l2 | 二级审批 |
| `admin` | 系统管理员 | admin | 运维台、代审批（不能同一人两级） |

演示页面顶部一键切换身份。

---

## 4. 十分钟演示脚本

1. **正常流程**：以 `student1` 提交一条申请 → 切换 `approver1` 通过 → 切换 `approver2` 通过。
   观察申请变为「已批准」，同步状态先显示「同步中」，后台任务执行后变为「已写回 M1」。
2. **跨模块效果**：打开运维台，M1 当前限制里能看到 `M2:<application_id>`；M1 调用日志里有 `M2_OCCUPY`。
3. **冲突检查（M2 内部）**：`student2` 提交同一教室重叠时段 → 两级审批都通过后，第二份在二级审批被 409 拦截，状态保持「待二级审批」。
4. **冲突检查（M1 侧）**：运维台点「在 M1 制造个人预约」→ 以学生身份提交同教室、明天 14:00–16:00 的申请 → 一级通过、二级审批返回 409「M1 侧冲突」，状态不变。
5. **失败一致性（重点）**：运维台打开「模拟 M1 不可用」→ 新申请走完两级审批 → 同步记录变红、错误可见，但 M2 不谎报成功 → 关闭故障注入 → 点「重试」或等后台任务 → 变绿。
6. **超时撤销**：提交后不审批，等待 90 秒（或点「立即执行超时扫描」），申请自动变为「超时撤销」，审批轨迹出现系统记录。
7. **撤销释放**：对已批准、尚未开始的申请点「撤销并释放」→ 同步记录出现 `RELEASE` → M1 限制被置为解除。
8. **工程证据**：`docker compose ps`、`/docs`、`/version`、CI 配置、Alembic 迁移。

---

## 5. 关键设计

### 5.1 审批状态机

```text
PENDING_L1 ──一级通过──▶ PENDING_L2 ──二级通过──▶ APPROVED
    │                        │                        │
    ├─一级驳回─▶ REJECTED     ├─二级驳回─▶ REJECTED     └─开始前撤销─▶ CANCELLED（写回 RELEASE）
    ├─申请人撤销─▶ CANCELLED   ├─申请人撤销─▶ CANCELLED
    └─超时────▶ EXPIRED      └─超时────▶ EXPIRED
```

### 5.2 M2 → M1 一致性：Outbox

审批通过与 `sync_records(OCCUPY, PENDING)` **在同一事务**写入。后台任务调用 M1：

- 成功 → `SUCCEEDED`
- 网络错误 / 5xx → `FAILED`，按 2/4/8/16/32 秒退避重试
- 4xx（冲突、鉴权、参数错误）或 M1 返回 `active=0` → 直接 `DEAD`，不做无意义重试
- 超过最大次数 → `DEAD`，运维台可人工重试

这样避免「M2 显示成功、M1 没有占用」的静默不一致：审批结果和同步结果分开呈现。

### 5.3 冲突控制

- 提交时：检查 M2 已批准占用，提前给出 409。
- 二级审批通过前：
  1. 对 `room_locks` 行执行 `SELECT … FOR UPDATE`（PostgreSQL），在锁内检查 M2 数据库冲突；
  2. 调用 M1 `GET /api/seats` 做只读预检，发现 `M1:个人预约` / `M2:*` / `M3:*` 直接 409；
  3. M1 临时不可用时**不阻塞审批**，继续写 outbox，由后台重试；预检与写回之间的竞态由 outbox 的 409 永久失败兜底。
- SQLite 测试环境无行锁，顺序用例仍可验证规则。

### 5.4 超时撤销

`expires_at` 在提交时写入；后台线程每 3 秒扫描一次（`M2_SYNC_INTERVAL_SECONDS`），不依赖页面刷新。
生产多实例部署时应改为数据库抢占式任务（`FOR UPDATE SKIP LOCKED`）或独立 scheduler，当前实现适合单实例演示。

---

## 6. 目录结构

```text
m2-demo/
├── backend/services/
│   ├── m2/                 # 本次交付的 M2 服务（FastAPI + SQLAlchemy + Alembic）
│   │   ├── app/            # 业务代码：状态机、审批、outbox、定时任务
│   │   ├── alembic/        # 版本化迁移
│   │   ├── tests/          # pytest
│   │   └── web/            # 无构建依赖的演示页面
│   └── m1_mock/            # M1 模拟（内存），接口镜像 A 的 PR #1，用于 Compose/CI
├── deploy/compose.yml
├── scripts/smoke.py
├── docs/                   # 需求、ADR、接口契约、讨论提纲
└── .github/workflows/      # CI 与部署模板
```

---

## 7. 已知限制（明天讨论用）

1. `m1_mock` 数据仅存内存，重启即清空；正式实现由成员 A 提供，M2 已按 A 的 PR #1 适配，可直接切换 `M2_M1_BASE_URL`。
2. 演示登录不校验密码，只用于展现 RBAC 流程；正式认证由 C 负责。
3. M2 写入的是教室级 `M2:<application_id>` 限制，不写座位级记录；A 的 M1 用 restriction 计算座位可用性。
4. 同步重试采用进程内定时线程，多副本部署需要换成数据库任务或独立 worker。
5. 演示页面为原生 HTML/JS，正式前端按项目统一方案（Vue 3 + Element Plus）重写。
6. 未实现审批通知（站内信/邮件），依赖 C 的公共通知服务。
7. M1 的 `X-Demo-User: admin` 和“北京时间无时区”都是 A 的 demo 约定；正式联调需 C 的服务认证，并建议 M1 接受带 offset 的时间。

详细需求、验收标准与待确认问题见：

- [`docs/requirements/m2.md`](docs/requirements/m2.md)
- [`docs/api/m1-m2-contract.md`](docs/api/m1-m2-contract.md)
- [`docs/discussion.md`](docs/discussion.md)
- [`docs/adr/`](docs/adr/)

---

## 8. 测试

```bash
cd backend/services/m2
../../../.venv/bin/ruff check .
../../../.venv/bin/pytest
```

结果：`25 passed`。

迁移从空库验证：

```bash
cd backend/services/m2
M2_DATABASE_URL=sqlite:////tmp/m2_empty.db ../../../.venv/bin/alembic upgrade head
M2_DATABASE_URL=sqlite:////tmp/m2_empty.db ../../../.venv/bin/alembic check
```
