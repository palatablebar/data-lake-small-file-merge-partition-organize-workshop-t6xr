"""Daily cross-table orchestration service.

流程：
  1. 刷新所有湖表的分区健康度，逐分区跑一次 FFD 装箱，拿到可节省文件数等口径；
  2. app.compaction.orchestration.allocate 在固定预算内做跨表分配
     （健康度差 + 可节省文件多者优先，单表最多一半预算，最新分区不执行）；
  3. 同一天重复生成直接返回已落库批次——不新增编排、预算去向保持不变；
  4. 提交时为每张入选表生成一张 CompactPlan（占用该用户的每日计划配额），
     再逐表投递 Celery 合并任务；worker 完成后各自生成 manifest 快照，
     本服务回写 item 的 plan/job/snapshot 与批次状态。
"""
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.compaction.orchestration import allocate
from app.config.settings import settings
from app.constants.error_codes import AppError, ErrorCode
from app.constants.file_format import TableStatus
from app.constants.orchestration_status import (
    OrchestrationItemStatus,
    OrchestrationStatus,
)
from app.constants.plan_status import PlanStatus
from app.middlewares.quota import acquire_table_lock, quota_snapshot
from app.models import (
    CompactJob,
    CompactPlan,
    LakeTable,
    OrchestrationBatch,
    OrchestrationItem,
    TableSnapshot,
    User,
)
from app.schemas.common import iso
from app.services import partition_service, plan_service, table_service
from app.utils.logger import log_event

BATCH_NOTE_PREFIX = "nightly-orchestration"


# --------------------------------------------------------------------------- #
# candidate evaluation
# --------------------------------------------------------------------------- #
def _partition_candidate(db: Session, table: LakeTable, spec) -> dict:
    """Run the FFD packing for one partition to derive its savings potential."""
    base = {
        "table_id": table.id,
        "table_fq_name": table.fq_name,
        "partition_value": spec.partition_value,
        "health_score": int(spec.health_score),
        "health_band": spec.health_band,
        "risk": spec.risk,
        "file_count": int(spec.file_count),
        "small_file_count": int(spec.small_file_count),
        "saved_files": 0,
        "candidate_file_count": int(spec.small_file_count),
        "rewrite_groups": 0,
        "rewritten_bytes": 0,
        "est_duration_seconds": 0.0,
        "est_cost_yuan": 0.0,
        "paused": table.status != TableStatus.ACTIVE.value,
    }
    if table.status != TableStatus.ACTIVE.value or spec.risk == "hot" or spec.small_file_count <= 0:
        return base
    try:
        result = plan_service.build_plan_result(db, table, [spec.partition_value], refresh=False)
    except AppError as exc:
        if exc.code == ErrorCode.NO_SMALL_FILES.value:
            base["small_file_count"] = 0
            return base
        raise
    base.update(
        {
            "saved_files": int(result["saved_files"]),
            "candidate_file_count": int(result["candidate_file_count"]),
            "rewrite_groups": int(result["rewrite_groups"]),
            "rewritten_bytes": int(result["rewritten_bytes"]),
            "est_duration_seconds": float(result["cost"]["est_duration_seconds"]),
            "est_cost_yuan": float(result["cost"]["est_cost_yuan"]),
        }
    )
    return base


def collect_candidates(db: Session) -> tuple[list[LakeTable], list[dict]]:
    tables = db.query(LakeTable).order_by(LakeTable.id.asc()).all()
    candidates: list[dict] = []
    for table in tables:
        specs = partition_service.recompute_table(db, table)
        for spec in sorted(specs, key=lambda s: s.partition_value):
            candidates.append(_partition_candidate(db, table, spec))
    return tables, candidates


def _budget_calibre() -> tuple[int, int]:
    budget = int(settings.ORCHESTRATION_REWRITE_BUDGET)
    max_per_table = max(1, int(budget * float(settings.ORCHESTRATION_MAX_TABLE_RATIO)))
    return budget, min(max_per_table, budget)


# --------------------------------------------------------------------------- #
# batch generation (idempotent per day)
# --------------------------------------------------------------------------- #
def get_batch_or_404(db: Session, batch_id: int) -> OrchestrationBatch:
    batch = db.get(OrchestrationBatch, batch_id)
    if batch is None:
        raise AppError(ErrorCode.ORCHESTRATION_NOT_FOUND)
    return batch


def generate_batch(db: Session, user: User, payload) -> dict:
    today = date.today()
    existing = (
        db.query(OrchestrationBatch)
        .filter(OrchestrationBatch.batch_date == today)
        .order_by(OrchestrationBatch.id.desc())
        .first()
    )
    if existing is not None:
        # 同一候选重复运行：不新增编排，返回的预算去向保持不变
        log_event("ORCHESTRATION_IDEMPOTENT_HIT", batch_id=existing.id)
        return {"batch": serialize_batch(db, existing), "idempotent": True, "quota": quota_snapshot(user)}

    budget_total, max_per_table = _budget_calibre()
    if payload and getattr(payload, "rewrite_budget", None):
        budget_total = max(1, int(payload.rewrite_budget))
        max_per_table = max(1, min(int(budget_total * float(settings.ORCHESTRATION_MAX_TABLE_RATIO)), budget_total))

    tables, candidates = collect_candidates(db)
    allocation = allocate(candidates, budget_total=budget_total, max_per_table=max_per_table)

    batch = OrchestrationBatch(
        batch_date=today,
        status=OrchestrationStatus.EMPTY.value if allocation["budget_used"] == 0 else OrchestrationStatus.PLANNED.value,
        budget_total=allocation["budget_total"],
        budget_used=allocation["budget_used"],
        max_per_table=allocation["max_per_table"],
        fingerprint=allocation["fingerprint"],
        totals=allocation["totals"],
        note=payload.note if payload and getattr(payload, "note", None) else f"{BATCH_NOTE_PREFIX}:{today.isoformat()}",
        created_by=user.id,
    )
    db.add(batch)
    db.flush()

    table_map = {table.id: table for table in tables}
    for rank, item in enumerate(allocation["items"], start=1):
        table = table_map[item["table_id"]]
        status = (
            OrchestrationItemStatus.SELECTED.value
            if item["selected"]
            else OrchestrationItemStatus.SKIPPED.value
        )
        db.add(
            OrchestrationItem(
                batch_id=batch.id,
                table_id=table.id,
                status=status,
                rank=item["rank"] if item["rank"] is not None else rank,
                selected_partitions=item["selected_partitions"],
                partition_decisions=item["partition_decisions"],
                metrics=item["metrics"],
                reason_code=item["reason_code"],
                reason_detail={} if item["selected"] else {"table_fq_name": table.fq_name},
            )
        )
    db.commit()
    db.refresh(batch)
    log_event(
        "ORCHESTRATION_GENERATED",
        batch_id=batch.id,
        budget_used=batch.budget_used,
        tables_selected=allocation["totals"]["tables_selected"],
    )
    return {"batch": serialize_batch(db, batch), "idempotent": False, "quota": quota_snapshot(user)}


# --------------------------------------------------------------------------- #
# submission: plans + per-table jobs, each table keeps its own snapshot
# --------------------------------------------------------------------------- #
def submit_batch(db: Session, user: User, batch_id: int) -> dict:
    batch = get_batch_or_404(db, batch_id)
    if batch.status not in (OrchestrationStatus.PLANNED.value, OrchestrationStatus.EMPTY.value):
        raise AppError(
            ErrorCode.ORCHESTRATION_NOT_SUBMITTABLE,
            detail={"batch_id": batch.id, "status": batch.status},
        )

    items = [item for item in batch.items if item.status == OrchestrationItemStatus.SELECTED.value]
    if not items:
        raise AppError(ErrorCode.ORCHESTRATION_EMPTY)

    # 配额按入选表数一次性校验/消费，避免半路上把额度吃光
    today = date.today()
    if user.plan_used_date != today:
        user.plan_used_date = today
        user.plan_used = 0
    if user.plan_used + len(items) > int(user.daily_plan_quota):
        raise AppError(
            ErrorCode.QUOTA_EXHAUSTED,
            detail={
                "quota": user.daily_plan_quota,
                "used": user.plan_used,
                "required": len(items),
                "retry_after": 3600,
            },
        )

    # 先全部生成计划并占配额，再统一投递，保证失败时状态可解释
    plan_by_item: dict[int, CompactPlan] = {}
    for item in items:
        table = table_service.get_table_or_404(db, item.table_id)
        if table.status != TableStatus.ACTIVE.value:
            raise AppError(ErrorCode.TABLE_PAUSED, detail={"table_id": table.id})
        result = plan_service.build_plan_result(db, table, list(item.selected_partitions))
        plan = plan_service.persist_plan(
            db,
            table,
            result,
            user,
            note=f"{BATCH_NOTE_PREFIX} batch#{batch.id} · {table.fq_name}",
        )
        user.plan_used += 1
        item.plan_id = plan.id
        plan_by_item[item.id] = plan
    db.commit()

    dispatched: list[dict] = []
    failed_locks: list[dict] = []
    for item in items:
        table = table_service.get_table_or_404(db, item.table_id)
        plan = plan_by_item[item.id]
        if not acquire_table_lock(table.id):
            item.status = OrchestrationItemStatus.FAILED.value
            item.failure_reason = "该湖表已有合并任务在执行，编排投递时未取得表锁"
            failed_locks.append({"table_id": table.id, "table_fq_name": table.fq_name})
            continue
        job = CompactJob(
            plan_id=plan.id,
            table_id=table.id,
            status="queued",
            progress=0,
            groups_total=len(plan.groups or []),
            triggered_by=user.id,
        )
        try:
            db.add(job)
            plan.status = "executing"
            item.status = OrchestrationItemStatus.EXECUTING.value
            db.commit()
            db.refresh(job)
            # capture scalar ids first: rollback() below expires the ORM objects
            job_id, plan_id = job.id, plan.id
            from app.tasks.compact_job import run_compaction_job

            async_result = run_compaction_job.delay(job_id)
            job.celery_task_id = async_result.id
            item.job_id = job_id
            db.commit()
            dispatched.append({"item_id": item.id, "job_id": job_id, "table_fq_name": table.fq_name})
        except Exception as exc:  # broker down: fail visibly, release the lock
            db.rollback()
            from app.middlewares.quota import release_table_lock

            release_table_lock(table.id)
            # the first commit already persisted plan=executing + a queued job;
            # re-query by the captured ids and mark both failed (no orphaned queued job)
            stale_plan = db.get(CompactPlan, plan_id)
            if stale_plan is not None:
                stale_plan.status = PlanStatus.FAILED.value
            stale_job = db.get(CompactJob, job_id)
            if stale_job is not None:
                stale_job.status = "failed"
                stale_job.failure_reason = f"任务投递失败: {type(exc).__name__}"
                stale_job.finished_at = datetime.now()
            item.job_id = job_id
            item.status = OrchestrationItemStatus.FAILED.value
            item.failure_reason = f"任务投递失败: {type(exc).__name__}"
            db.commit()
            failed_locks.append({"table_id": table.id, "table_fq_name": table.fq_name})
            log_event("ORCHESTRATION_DISPATCH_FAILED", batch_id=batch.id, reason=str(exc))

    batch.submitted_by = user.id
    batch.submitted_at = datetime.now()
    batch.status = _derive_batch_status(db, batch)
    db.commit()
    db.refresh(batch)
    log_event(
        "ORCHESTRATION_SUBMITTED",
        batch_id=batch.id,
        dispatched=len(dispatched),
        failed_locks=failed_locks,
    )
    return {
        "batch": serialize_batch(db, batch),
        "dispatched": dispatched,
        "blocked": failed_locks,
        "quota": quota_snapshot(user),
    }


# --------------------------------------------------------------------------- #
# status rollup (worker writes jobs/snapshots; we read them back)
# --------------------------------------------------------------------------- #
def refresh_batch_status(db: Session, batch: OrchestrationBatch) -> OrchestrationBatch:
    """Reconcile item states with their jobs/snapshots and roll up batch status."""
    changed = False
    for item in batch.items:
        if item.status == OrchestrationItemStatus.SKIPPED.value or item.job_id is None:
            continue
        job = db.get(CompactJob, item.job_id)
        if job is None:
            continue
        if job.status == "succeeded":
            snapshot = (
                db.query(TableSnapshot)
                .filter(TableSnapshot.job_id == job.id)
                .order_by(TableSnapshot.id.desc())
                .first()
            )
            if snapshot is not None and item.snapshot_id != snapshot.id:
                item.snapshot_id = snapshot.id
                changed = True
            if item.status != OrchestrationItemStatus.COMPLETED.value:
                item.status = OrchestrationItemStatus.COMPLETED.value
                changed = True
        elif job.status == "failed":
            if item.status != OrchestrationItemStatus.FAILED.value:
                item.status = OrchestrationItemStatus.FAILED.value
                item.failure_reason = job.failure_reason
                changed = True
        elif job.status in ("queued", "running"):
            if item.status != OrchestrationItemStatus.EXECUTING.value:
                item.status = OrchestrationItemStatus.EXECUTING.value
                changed = True
    new_status = _derive_batch_status(db, batch)
    if new_status != batch.status:
        batch.status = new_status
        changed = True
    if changed:
        db.commit()
        db.refresh(batch)
    return batch


def _derive_batch_status(db: Session, batch: OrchestrationBatch) -> str:
    actionable = [item for item in batch.items if item.status != OrchestrationItemStatus.SKIPPED.value]
    if not actionable:
        return OrchestrationStatus.EMPTY.value
    statuses = {item.status for item in actionable}
    if statuses == {OrchestrationItemStatus.SELECTED.value}:
        return OrchestrationStatus.PLANNED.value
    if OrchestrationItemStatus.FAILED.value in statuses:
        # 有失败即部分失败（其余表可能仍在跑或已完成）
        return OrchestrationStatus.PARTIAL_FAILED.value
    if statuses == {OrchestrationItemStatus.COMPLETED.value}:
        return OrchestrationStatus.COMPLETED.value
    if OrchestrationItemStatus.EXECUTING.value in statuses or OrchestrationItemStatus.SELECTED.value in statuses:
        return OrchestrationStatus.EXECUTING.value
    return OrchestrationStatus.PLANNED.value


# --------------------------------------------------------------------------- #
# read models
# --------------------------------------------------------------------------- #
def list_batches(db: Session, limit: int = 20) -> list[dict]:
    rows = db.query(OrchestrationBatch).order_by(OrchestrationBatch.id.desc()).limit(limit).all()
    return [serialize_batch(db, refresh_batch_status(db, batch)) for batch in rows]


def serialize_item(db: Session, item: OrchestrationItem) -> dict:
    table = db.get(LakeTable, item.table_id)
    plan = db.get(CompactPlan, item.plan_id) if item.plan_id else None
    job = db.get(CompactJob, item.job_id) if item.job_id else None
    snapshot = db.get(TableSnapshot, item.snapshot_id) if item.snapshot_id else None
    return {
        "id": item.id,
        "table_id": item.table_id,
        "table_fq_name": table.fq_name if table else None,
        "status": item.status,
        "rank": item.rank,
        "selected_partitions": list(item.selected_partitions or []),
        "partition_decisions": list(item.partition_decisions or []),
        "metrics": item.metrics or {},
        "reason_code": item.reason_code,
        "plan_id": item.plan_id,
        "plan_status": plan.status if plan else None,
        "job_id": item.job_id,
        "job_status": job.status if job else None,
        "job_progress": int(job.progress) if job else None,
        "snapshot_id": item.snapshot_id,
        "snapshot_version": snapshot.version if snapshot else None,
        "failure_reason": item.failure_reason,
    }


def serialize_batch(db: Session, batch: OrchestrationBatch) -> dict:
    items = [serialize_item(db, item) for item in sorted(batch.items, key=lambda i: i.rank)]
    selected = [item for item in items if item["status"] != OrchestrationItemStatus.SKIPPED.value]
    skipped = [item for item in items if item["status"] == OrchestrationItemStatus.SKIPPED.value]
    return {
        "id": batch.id,
        "batch_date": batch.batch_date.isoformat(),
        "status": batch.status,
        "budget_total": int(batch.budget_total),
        "budget_used": int(batch.budget_used),
        "budget_remaining": int(batch.budget_total) - int(batch.budget_used),
        "max_per_table": int(batch.max_per_table),
        "fingerprint": batch.fingerprint,
        "totals": batch.totals or {},
        "note": batch.note,
        "created_by": batch.created_by,
        "submitted_by": batch.submitted_by,
        "submitted_at": iso(batch.submitted_at),
        "created_at": iso(batch.created_at),
        "items": items,
        "selected_count": len(selected),
        "skipped_count": len(skipped),
    }
