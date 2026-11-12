export type OrchestrationBatchStatus =
  | 'planned'
  | 'executing'
  | 'completed'
  | 'partial_failed'
  | 'empty'

export type OrchestrationItemStatus = 'selected' | 'executing' | 'completed' | 'failed' | 'skipped'

export type OrchestrationReasonCode =
  | 'hot_partition_excluded'
  | 'no_rewrite_savings'
  | 'budget_exhausted'
  | 'table_cap_reached'
  | 'table_paused'
  | 'no_small_files'

export interface PartitionDecision {
  table_id: number
  table_fq_name: string | null
  partition_value: string
  selected: boolean
  reason_code: OrchestrationReasonCode | null
  rank: number | null
  health_score: number
  health_band: string
  risk: 'normal' | 'hot'
  file_count: number
  small_file_count: number
  saved_files: number
  candidate_file_count: number
  rewrite_groups: number
  rewritten_bytes: number
  est_duration_seconds: number
  est_cost_yuan: number
}

export interface OrchestrationMetrics {
  partitions_selected: number
  candidate_file_count: number
  saved_files: number
  rewrite_groups: number
  rewritten_bytes: number
  est_duration_seconds: number
  est_cost_yuan: number
}

export interface OrchestrationItem {
  id: number
  table_id: number
  table_fq_name: string | null
  status: OrchestrationItemStatus
  rank: number
  selected_partitions: string[]
  partition_decisions: PartitionDecision[]
  metrics: OrchestrationMetrics
  reason_code: OrchestrationReasonCode | null
  plan_id: number | null
  plan_status: string | null
  job_id: number | null
  job_status: string | null
  job_progress: number | null
  snapshot_id: number | null
  snapshot_version: number | null
  failure_reason: string | null
}

export interface BatchTotals extends OrchestrationMetrics {
  tables_total: number
  tables_selected: number
  candidate_partitions: number
  partitions_selected: number
}

export interface OrchestrationBatch {
  id: number
  batch_date: string
  status: OrchestrationBatchStatus
  budget_total: number
  budget_used: number
  budget_remaining: number
  max_per_table: number
  fingerprint: string
  totals: BatchTotals
  note: string | null
  created_by: number | null
  submitted_by: number | null
  submitted_at: string | null
  created_at: string | null
  items: OrchestrationItem[]
  selected_count: number
  skipped_count: number
}

export interface OrchestrationGenerateResult {
  batch: OrchestrationBatch
  idempotent: boolean
  quota: {
    daily_plan_quota: number
    plan_used: number
    quota_remaining: number
    plan_used_date: string | null
    window_max: number
    window_seconds: number
  }
}

export interface OrchestrationSubmitResult {
  batch: OrchestrationBatch
  dispatched: { item_id: number; job_id: number; table_fq_name: string }[]
  blocked: { table_id: number; table_fq_name: string }[]
  quota: OrchestrationGenerateResult['quota']
}
