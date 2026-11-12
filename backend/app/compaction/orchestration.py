"""Cross-table nightly orchestration allocation (pure Python, no DB).

夜间固定重写预算的跨表分配器。输入是各表分区候选（健康度、FFD 可节省文件数
等已由服务层算好），输出确定性的入选/未入选决策：

* 最新分区（risk=hot）一律不执行；暂停表、无小文件、装箱后无可节省文件的分区
  同样剔除，并给出机器可读的 reason_code。
* 合格候选按 (health_score 升序, saved_files 降序, table_id, partition_value)
  排序——先救健康度最差、可回收文件最多的旧碎片分区。
* 总预算 budget_total 个分区，单表上限 max_per_table（默认预算的一半）；
  撞单表上限记 table_cap_reached，全局预算用尽记 budget_exhausted。
* 全程确定性：相同候选集合与预算口径必得相同决策与 fingerprint，重复运行
  由服务层直接返回同一批次，预算去向不变。
"""
from __future__ import annotations

import hashlib
import json

from app.constants.orchestration_status import (
    REASON_BUDGET_EXHAUSTED,
    REASON_HOT,
    REASON_NO_SMALL_FILES,
    REASON_NO_SAVINGS,
    REASON_TABLE_CAP,
    REASON_TABLE_PAUSED,
)

# 资格类剔除（与预算无关），排在预算类原因之前
ELIGIBILITY_REASONS = (
    REASON_TABLE_PAUSED,
    REASON_HOT,
    REASON_NO_SMALL_FILES,
    REASON_NO_SAVINGS,
)


def exclusion_reason(candidate: dict) -> str | None:
    """Return the machine-readable reason a candidate may never be selected."""
    if candidate.get("paused"):
        return REASON_TABLE_PAUSED
    if candidate.get("risk") == "hot":
        return REASON_HOT
    if int(candidate.get("small_file_count") or 0) <= 0:
        return REASON_NO_SMALL_FILES
    if int(candidate.get("saved_files") or 0) <= 0:
        return REASON_NO_SAVINGS
    return None


def _rank_key(candidate: dict) -> tuple:
    return (
        int(candidate["health_score"]),
        -int(candidate["saved_files"]),
        int(candidate["table_id"]),
        str(candidate["partition_value"]),
    )


def allocate(candidates: list[dict], budget_total: int, max_per_table: int) -> dict:
    """Allocate the fixed rewrite budget across partition candidates.

    ``candidates`` item keys: table_id, table_fq_name, partition_value,
    health_score, health_band, risk, small_file_count, file_count,
    saved_files, candidate_file_count, rewrite_groups, rewritten_bytes,
    est_duration_seconds, est_cost_yuan, paused.
    """
    budget_total = max(1, int(budget_total))
    max_per_table = max(1, min(int(max_per_table), budget_total))

    decisions: dict[tuple[int, str], dict] = {}
    for candidate in candidates:
        key = (int(candidate["table_id"]), str(candidate["partition_value"]))
        reason = exclusion_reason(candidate)
        decisions[key] = {
            "table_id": int(candidate["table_id"]),
            "table_fq_name": candidate.get("table_fq_name"),
            "partition_value": str(candidate["partition_value"]),
            "selected": False,
            "reason_code": reason,
            "health_score": int(candidate.get("health_score") or 0),
            "health_band": candidate.get("health_band"),
            "risk": candidate.get("risk", "normal"),
            "file_count": int(candidate.get("file_count") or 0),
            "small_file_count": int(candidate.get("small_file_count") or 0),
            "saved_files": int(candidate.get("saved_files") or 0),
            "candidate_file_count": int(candidate.get("candidate_file_count") or 0),
            "rewrite_groups": int(candidate.get("rewrite_groups") or 0),
            "rewritten_bytes": int(candidate.get("rewritten_bytes") or 0),
            "est_duration_seconds": float(candidate.get("est_duration_seconds") or 0.0),
            "est_cost_yuan": float(candidate.get("est_cost_yuan") or 0.0),
        }

    eligible = [c for c in candidates if exclusion_reason(c) is None]
    eligible.sort(key=_rank_key)

    per_table_used: dict[int, int] = {}
    selected_keys: list[tuple[int, str]] = []
    budget_used = 0
    rank = 0
    for candidate in eligible:
        key = (int(candidate["table_id"]), str(candidate["partition_value"]))
        table_used = per_table_used.get(int(candidate["table_id"]), 0)
        if table_used >= max_per_table:
            decisions[key]["reason_code"] = REASON_TABLE_CAP
            continue
        if budget_used >= budget_total:
            decisions[key]["reason_code"] = REASON_BUDGET_EXHAUSTED
            continue
        rank += 1
        decisions[key]["selected"] = True
        decisions[key]["reason_code"] = None
        decisions[key]["rank"] = rank
        per_table_used[int(candidate["table_id"])] = table_used + 1
        selected_keys.append(key)
        budget_used += 1

    # 表级聚合（按入选排名、表 id 稳定排序）
    table_ids = sorted({int(c["table_id"]) for c in candidates})
    table_candidates: dict[int, list[dict]] = {tid: [] for tid in table_ids}
    for candidate in candidates:
        table_candidates[int(candidate["table_id"])].append(candidate)

    items: list[dict] = []
    for table_id in table_ids:
        rows = [decisions[(table_id, str(c["partition_value"]))] for c in table_candidates[table_id]]
        rows.sort(key=lambda row: (-(row["rank"] if row["selected"] else -1), row["partition_value"]))
        selected_rows = [row for row in rows if row["selected"]]
        selected_rows.sort(key=lambda row: row["rank"])
        table_reason = _table_reason(rows, table_candidates[table_id])
        metrics = _aggregate_metrics(selected_rows)
        items.append(
            {
                "table_id": table_id,
                "table_fq_name": table_candidates[table_id][0].get("table_fq_name"),
                "rank": selected_rows[0]["rank"] if selected_rows else None,
                "selected": bool(selected_rows),
                "reason_code": table_reason,
                "selected_partitions": [row["partition_value"] for row in selected_rows],
                "partition_decisions": [
                    {
                        **{key: row[key] for key in row if key != "rank"},
                        "rank": row.get("rank"),
                    }
                    for row in sorted(rows, key=lambda r: r["partition_value"])
                ],
                "metrics": metrics,
            }
        )
    items.sort(key=lambda item: (item["rank"] is None, item["rank"] if item["rank"] is not None else 1 << 30, item["table_id"]))

    selected_decisions = [decisions[key] for key in selected_keys]
    totals = _aggregate_metrics(selected_decisions)
    totals.update(
        {
            "tables_total": len(table_ids),
            "tables_selected": sum(1 for item in items if item["selected"]),
            "candidate_partitions": len(candidates),
            "partitions_selected": budget_used,
        }
    )

    fingerprint = _fingerprint(candidates, budget_total, max_per_table, selected_keys)
    return {
        "budget_total": budget_total,
        "max_per_table": max_per_table,
        "budget_used": budget_used,
        "budget_remaining": budget_total - budget_used,
        "per_table_used": per_table_used,
        "selected_keys": selected_keys,
        "items": items,
        "totals": totals,
        "fingerprint": fingerprint,
    }


def _table_reason(rows: list[dict], raw_candidates: list[dict]) -> str | None:
    if any(row["selected"] for row in rows):
        return None
    if any(c.get("paused") for c in raw_candidates):
        return REASON_TABLE_PAUSED
    reasons = [row["reason_code"] for row in rows if row["reason_code"]]
    if not reasons:
        return None
    # 多数决；平票时按资格类优先、预算类兜底的固定顺序
    order = list(ELIGIBILITY_REASONS) + [REASON_TABLE_CAP, REASON_BUDGET_EXHAUSTED]
    return sorted(reasons, key=lambda code: (-reasons.count(code), order.index(code)))[0]


def _aggregate_metrics(rows: list[dict]) -> dict:
    return {
        "partitions_selected": len(rows),
        "candidate_file_count": sum(int(row.get("candidate_file_count") or 0) for row in rows),
        "saved_files": sum(int(row.get("saved_files") or 0) for row in rows),
        "rewrite_groups": sum(int(row.get("rewrite_groups") or 0) for row in rows),
        "rewritten_bytes": sum(int(row.get("rewritten_bytes") or 0) for row in rows),
        "est_duration_seconds": round(
            sum(float(row.get("est_duration_seconds") or 0.0) for row in rows), 1
        ),
        "est_cost_yuan": round(sum(float(row.get("est_cost_yuan") or 0.0) for row in rows), 2),
    }


def _fingerprint(candidates: list[dict], budget_total: int, max_per_table: int, selected_keys: list[tuple]) -> str:
    canonical_candidates = sorted(
        (
            {
                "table_id": int(c["table_id"]),
                "partition_value": str(c["partition_value"]),
                "health_score": int(c.get("health_score") or 0),
                "small_file_count": int(c.get("small_file_count") or 0),
                "saved_files": int(c.get("saved_files") or 0),
                "risk": c.get("risk", "normal"),
                "paused": bool(c.get("paused")),
            }
            for c in candidates
        ),
        key=lambda row: (row["table_id"], row["partition_value"]),
    )
    canonical = {
        "budget_total": int(budget_total),
        "max_per_table": int(max_per_table),
        "candidates": canonical_candidates,
        "selected": [[tid, value] for tid, value in selected_keys],
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]
