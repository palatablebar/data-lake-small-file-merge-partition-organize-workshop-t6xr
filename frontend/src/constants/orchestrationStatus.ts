import type { OrchestrationBatchStatus, OrchestrationItemStatus, OrchestrationReasonCode } from '../types/orchestration'

export const ORCHESTRATION_BATCH_STATUS_LABELS: Record<OrchestrationBatchStatus, string> = {
  planned: '待执行',
  executing: '执行中',
  completed: '已完成',
  partial_failed: '部分失败',
  empty: '空编排',
}

export const ORCHESTRATION_ITEM_STATUS_LABELS: Record<OrchestrationItemStatus, string> = {
  selected: '已入选',
  executing: '执行中',
  completed: '已完成',
  failed: '失败',
  skipped: '未入选',
}

export const ORCHESTRATION_REASON_LABELS: Record<OrchestrationReasonCode, string> = {
  hot_partition_excluded: '最新分区不执行（HOT 保护）',
  no_rewrite_savings: '装箱后无可节省文件，无需重写',
  budget_exhausted: '当日重写预算已被更高优先级分区占满',
  table_cap_reached: '单表最多占用一半预算，已达上限',
  table_paused: '湖表已暂停',
  no_small_files: '分区内没有低于阈值的小文件',
}

export const ACTIVE_BATCH_STATUSES: OrchestrationBatchStatus[] = ['planned', 'executing']

export function batchStatusTagClass(status: OrchestrationBatchStatus): string {
  switch (status) {
    case 'completed':
      return 'bp-tag--ok'
    case 'partial_failed':
      return 'bp-tag--hot'
    case 'executing':
      return 'bp-tag--watch'
    default:
      return 'bp-tag--muted'
  }
}

export function itemStatusTagClass(status: OrchestrationItemStatus): string {
  switch (status) {
    case 'completed':
      return 'bp-tag--ok'
    case 'failed':
      return 'bp-tag--hot'
    case 'executing':
      return 'bp-tag--watch'
    case 'selected':
      return 'bp-tag--watch'
    default:
      return 'bp-tag--muted'
  }
}
