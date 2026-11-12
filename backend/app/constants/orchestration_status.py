"""Daily cross-table orchestration lifecycle.

Mirrored on the frontend in src/constants/orchestrationStatus.ts.
"""
from enum import Enum


class OrchestrationStatus(str, Enum):
    # 批次已生成、计划已落库，等待统一提交
    PLANNED = "planned"
    # 至少一个表的合并任务已投递，仍有任务在排队/运行
    EXECUTING = "executing"
    # 所有入选表的任务都成功，且每表都留下了快照
    COMPLETED = "completed"
    # 存在失败任务（其余表可能已完成）
    PARTIAL_FAILED = "partial_failed"
    # 没有任何可入选分区，批次为空
    EMPTY = "empty"


class OrchestrationItemStatus(str, Enum):
    # 入选：已为该表生成合并计划，等待执行
    SELECTED = "selected"
    # 任务已投递（queued/running）
    EXECUTING = "executing"
    # 任务成功且快照已生成
    COMPLETED = "completed"
    # 任务失败
    FAILED = "failed"
    # 未入选：预算或资格原因（见 reason_code）
    SKIPPED = "skipped"


ORCHESTRATION_STATUS_LABELS = {
    OrchestrationStatus.PLANNED: "待执行",
    OrchestrationStatus.EXECUTING: "执行中",
    OrchestrationStatus.COMPLETED: "已完成",
    OrchestrationStatus.PARTIAL_FAILED: "部分失败",
    OrchestrationStatus.EMPTY: "空编排",
}

# 未入选原因口径（前端按 code 做文案映射）
REASON_HOT = "hot_partition_excluded"
REASON_NO_SAVINGS = "no_rewrite_savings"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_TABLE_CAP = "table_cap_reached"
REASON_TABLE_PAUSED = "table_paused"
REASON_NO_SMALL_FILES = "no_small_files"

REASON_LABELS = {
    REASON_HOT: "最新分区不执行（HOT 保护）",
    REASON_NO_SAVINGS: "装箱后无可节省文件，无需重写",
    REASON_BUDGET_EXHAUSTED: "当日重写预算已被更高优先级分区占满",
    REASON_TABLE_CAP: "单表最多占用一半预算，已达上限",
    REASON_TABLE_PAUSED: "湖表已暂停",
    REASON_NO_SMALL_FILES: "分区内没有低于阈值的小文件",
}
