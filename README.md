# lakePack · 数据湖小文件合并与分区整理工坊

拾光内容平台离线数仓小队（4 人）的湖表整理工作台：**分区健康度 → 当日跨表预算编排 → FFD 小文件合并计划 → Celery 执行重写 → manifest 快照守恒校验**，全部发生在后端 Python 真算并持久化，前端只做展示与交互。

- 模拟客户：内容平台「拾光」，S3 兼容对象存储 40TB，Parquet 表 60 张，Flink 每小时落小文件。
- 现有工作流：凌晨人工跑 Spark 合并脚本。痛点：① 小文件百万级、元数据查询慢；② 合并后历史快照对不上、无法审计；③ 任务 OOM 或重复跑覆盖正在写入的分区。
- 本系统替代：文件清单/分区健康度/合并计划/执行与快照校验；下游 Presto 查询与 BI。

## 快速启动

```bash
cp .env.example .env      # 已带好演示默认值
docker compose up -d --build
docker compose ps         # 5 个服务都应 healthy
# 启动时 SEED_DEMO=true 会自动跑一次 seed；也可以手动再跑（幂等）：
docker compose exec -T backend python -m scripts.seed_demo
```

访问：

| 入口 | 地址 |
| --- | --- |
| 前端（Nginx） | http://localhost:18221 |
| 后端 API / Swagger | http://localhost:19221/docs |
| MySQL | localhost:17721（库 `lakepack_db`） |

演示账号（密码统一 `Demo@2026`）：

| 角色 | 邮箱 | 权限 |
| --- | --- | --- |
| 平台管理员 `admin` | `admin@lake.local` | 全部页面；登记湖表、改目标大小、看审计；可生成/执行计划 |
| 数据工程师 `engineer` | `eng@lake.local` | 生成计划、执行合并、创建快照 |
| 分析师 `analyst` | `analyst@lake.local` | 只读：健康度、计划、快照 diff；生成/执行返回 403 |

## 核心功能

- **湖表总览**：3 张湖表的分区健康热力条、小文件数、可回收文件数、平均健康度。
- **文件与分区明细**：分区文件数/小文件数/平均大小/健康度/HOT 风险；文件明细支持分区、大小档位、排序筛选。
- **合并计划预览**（算法 1）：选分区 → FFD 装箱 → 分组树、产出文件数、节省率、预计耗时/成本、hot 风险提示、幂等指纹。
- **当日跨表编排**（算法 3）：固定重写预算内跨表分配——按分区健康度与可节省文件数排优先级，单表最多占一半预算，最新分区不执行；同日重复生成幂等返回，预算去向不变；提交后逐表执行合并并各自留快照，页面完整展示预算去向、每表入选分区与未入选原因。
- **任务执行监控**：Celery worker 逐组重写 manifest、回报进度、自动生成快照；同一湖表 Redis 执行锁防并发。
- **快照 diff 与校验**（算法 2）：两版 manifest 的 added/removed/changed、Σrows 守恒、checksum 一致性。
- **横切**：JWT + 三角色、审计日志、Redis 滑动窗口限流 + 每日计划配额、统一错误码。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Vite + Pinia + vue-router，Nginx 托管并反代 `/api/` |
| 后端 | Python 3.11 + FastAPI + SQLAlchemy 2.0 + Alembic 目录 |
| 异步 | Celery 5 + Redis 7 |
| 数据库 | MySQL 8.0 |
| 视觉 | 工程蓝图：蓝灰纸底 `oklch(93% 0.01 240)` + 深蓝墨线 + 淡网格；Fraunces + Barlow；等距线框/标注引线 |

## 目录

```
backend/
  app/{config,constants,models,schemas,middlewares,services,controllers,routes}
  app/compaction/         # 算法：binpack.py / orchestration.py / manifest.py / cost_model.py（纯 Python）
  app/tasks/compact_job.py# Celery 执行任务
  scripts/seed_demo.py    # 幂等 seed
  alembic/                # 迁移目录（初始 revision 用 Base.metadata.create_all 同步）
worker/                   # Celery worker 镜像（复用 backend 代码）
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants,styles}
```

## 算法口径

### 算法 1：分区小文件 FFD 合并计划（`backend/app/compaction/binpack.py`）

- 候选：单分区内 `size < small_file_threshold_mb`（默认 64MB）的文件。
- 按 `(size desc, path asc)` 排序，first-fit-decreasing 装进 `target_file_size_mb`（默认 256MB）的箱。
- 箱内 ≥2 个文件 → `action=rewrite`（重写为 1 个产物）；单文件箱 → `action=carry`（保持原样）。
- 产出：`groups`、产出文件数、重写字节、文件数节省率、预计耗时 = 重写字节 / 80MB/s + 0.3s × 候选文件数、成本 = 重写GB × ¥0.85/GB。
- 热分区：分区值 = 该表最新分区 → `risk=hot`，计划中降权并提示延后。
- 指纹 `fingerprint`：对 (strategy, target, threshold, partitions, 每组文件路径) 做 SHA-256；相同输入再次生成 → `idempotent=true`。

### 算法 2：manifest diff 与守恒校验（`backend/app/compaction/manifest.py`）

- 输入两版 manifest：`path -> {size_bytes, rows, checksum, partition}`。
- 输出 added / removed / changed、Σrows 前后对比（`row_conserved`）、同路径 checksum 不一致清单、文件数与大小变化。
- 合并任务的语义约束：只允许新增产物 + 移除小文件，`changed` 必须为 0，`Σrows` 必须守恒。

### 算法 3：当日跨表编排与固定预算分配（`backend/app/compaction/orchestration.py`）

- 候选：逐表刷新分区健康度，对每个分区独立跑一次算法 1 的 FFD 装箱，得到该分区可节省文件数、重写分组、重写字节与预计成本。
- 资格剔除（与预算无关，逐分区给出 reason_code）：最新分区 `risk=hot` 一律不执行（`hot_partition_excluded`）；暂停表 `table_paused`；无小文件 `no_small_files`；装箱后无可节省文件 `no_rewrite_savings`。
- 优先级：合格候选按 `(health_score 升序, saved_files 降序, table_id, partition_value)` 排序——先救健康度最差、可回收文件最多的旧碎片分区，解决"旧高碎片分区长期排不到"。
- 预算口径：`ORCHESTRATION_REWRITE_BUDGET`（默认 6，单位=入选分区数）为当日固定重写预算；单表上限 `ORCHESTRATION_MAX_TABLE_RATIO`（默认 0.5）× 预算，即单表最多占一半。撞上限记 `table_cap_reached`，全局预算用尽记 `budget_exhausted`。
- 幂等：同一天重复调用直接返回已落库批次（`idempotent=true`），不新增编排、不重新分配，预算去向与首次完全一致；批次另带跨候选与预算口径的 SHA-256 `fingerprint`。第二天才会生成新批次。
- 提交：为每张入选表生成一张草稿 `CompactPlan`（按入选表数一次性校验/消耗该用户每日计划配额），再逐表投递 Celery 任务；各表 worker 完成后分别生成自己的 manifest 快照，批次状态与每表 plan/job/snapshot 自动回滚汇总。

### 分区健康度（`backend/app/services/partition_service.py`）

```
utilization   = min(1, avg_file_size / target_file_size)
fragmentation = min(1, file_count / 60)
score         = round(100 × (0.65 × utilization + 0.35 × (1 − fragmentation)))
band          = healthy(≥75) / watch(≥50) / degraded(<50)
```

## 模拟边界（诚实说明）

- 对象存储用**逻辑路径 + manifest 元数据**模拟（`s3://shiguang-lake/warehouse/...`），worker 不移动真实字节；产物的 size/rows 由计划模型推导，checksum 由 (path, rows, size) 确定性生成。所有 DB 状态、进度、快照都是真实持久化的。
- 表数量与文件规模按 seed 缩小（3 张表 / 约 280 个文件），原型中 60 张表与百万级小文件的口径一致，只是数量压缩。

## 环境变量（`.env.example`）

`COMPOSE_PROJECT_NAME=lakepack`、`DB_*`、`JWT_SECRET`、`FRONTEND_PORT=18221`、`BACKEND_PORT=19221`、`DB_PORT=17721`、`REDIS_URL`、`LAKE_BUCKET`、`BANDWIDTH_MBPS=80`、`PER_FILE_OVERHEAD_S=0.3`、`UNIT_COST_PER_GB=0.85`、`COST_PER_FILE_DAY=0.0004`、`PLAN_DAILY_QUOTA=8`、`ORCHESTRATION_REWRITE_BUDGET=6`、`ORCHESTRATION_MAX_TABLE_RATIO=0.5`、`RATE_WINDOW_*`、`SEED_DEMO=true`。

## 枚举出现位置

| 枚举 | 后端 | 前端 |
| --- | --- | --- |
| JobStatus `queued/running/succeeded/failed` | `app/constants/job_status.py`、`compact_job.py`、`job_service.py` | `src/constants/jobStatus.ts`、`types/job.ts` |
| PlanStatus `draft/executing/completed/failed` | `app/constants/plan_status.py`、`plan_service.py` | `src/constants/planStatus.ts`、`types/plan.ts` |
| OrchestrationBatchStatus `planned/executing/completed/partial_failed/empty`、OrchestrationItemStatus `selected/executing/completed/failed/skipped` | `app/constants/orchestration_status.py`、`orchestration_service.py` | `src/constants/orchestrationStatus.ts`、`types/orchestration.ts` |
| PartitionRisk `normal/hot` | `app/constants/partition_risk.py`、`partition_service.py` | `src/constants/partitionRisk.ts`、`types/table.ts` |
| HealthBand `healthy/watch/degraded` | `app/constants/health_band.py` | `src/constants/healthBand.ts` |
| FileFormat / TableStatus | `app/constants/file_format.py` | `src/types/table.ts` |
| 错误码 `LAKE-2xxx` | `app/constants/error_codes.py` | `src/constants/errorCodes.ts` |

编排未入选原因码（非 HTTP 错误，随分区决策返回）：`hot_partition_excluded / no_rewrite_savings / budget_exhausted / table_cap_reached / table_paused / no_small_files`，文案映射见 `orchestration_status.REASON_LABELS` 与前端 `ORCHESTRATION_REASON_LABELS`。

## 本地开发（不用 Docker）

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
DB_HOST=127.0.0.1 DB_PORT=17721 uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev   # http://localhost:18221，代理到 19221
```

## License

MIT（演示项目，数据均为虚构）。
