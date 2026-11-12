<template>
  <div>
    <PageHeader
      figure="FIG.05"
      section="nightly orchestration"
      title="当日跨表编排"
      description="夜间固定重写预算内统一编排多张湖表：按分区健康度与可节省文件数排优先级，单表最多占一半预算，最新分区不执行；提交后各表分别完成合并并各自留下 manifest 快照。"
    >
      <template #actions>
        <RouterLink to="/jobs" class="bp-btn">任务监控</RouterLink>
        <RouterLink to="/snapshots" class="bp-btn">快照校验</RouterLink>
        <button
          v-if="auth.canWrite"
          class="bp-btn bp-btn--solid"
          :disabled="store.generating"
          data-test="generate-orchestration"
          @click="generate"
        >
          {{ store.generating ? '编排中…' : '生成当日编排' }}
        </button>
      </template>
    </PageHeader>

    <ErrorNotice :error="store.error" />

    <div v-if="idempotentNotice" class="rise mt-4 border-l-2 border-[var(--sea)] bg-[color-mix(in_oklch,var(--sea)_9%,transparent)] px-3 py-2 text-[11.5px]">
      {{ idempotentNotice }}
    </div>

    <div v-if="!batch" class="bp-frame mt-6">
      <div class="bp-frame__head"><span class="label">orchestration ledger</span><span class="label">FIG.05-A</span></div>
      <div class="px-6 py-14 text-center">
        <div class="display-sm">今日尚未生成跨表编排</div>
        <p class="mx-auto mt-2 max-w-[460px] text-[12px] leading-relaxed text-[var(--ink-soft)]">
          点击「生成当日编排」后，系统会刷新全部湖表的分区健康度，逐分区做 FFD 装箱估算，
          并在固定预算内给出跨表分配。同一天重复生成不会新增编排，预算去向保持不变。
        </p>
        <button
          v-if="auth.canWrite"
          class="bp-btn bp-btn--solid mt-5"
          :disabled="store.generating"
          @click="generate"
        >
          {{ store.generating ? '编排中…' : '生成当日编排' }}
        </button>
        <div v-else class="leader-note mt-5 inline-block text-left">
          分析师为只读角色：可查看编排结果、预算去向与未入选原因，但不能生成或提交。
        </div>
      </div>
    </div>

    <template v-else>
      <!-- 批次头：预算去向总览 -->
      <section class="rise bp-frame mt-5" data-test="budget-ledger">
        <div class="bp-frame__head">
          <span class="label">预算去向 / budget destination · 批次 #{{ batch.id }} · {{ batch.batch_date }}</span>
          <span class="bp-tag" :class="batchStatusTagClass(batch.status)">{{ batchLabel(batch.status) }}</span>
        </div>
        <div class="bp-frame__body space-y-4">
          <div class="grid grid-cols-2 gap-px border border-[var(--rule-soft)] bg-[var(--rule-soft)] md:grid-cols-6">
            <div class="bg-[var(--paper-2)] px-3 py-2.5">
              <div class="label">固定预算</div>
              <div class="num mt-1 text-[18px] leading-none">{{ batch.budget_total }}</div>
              <div class="mt-1 text-[10px] text-[var(--ink-faint)]">分区数</div>
            </div>
            <div class="bg-[var(--paper-2)] px-3 py-2.5">
              <div class="label">已分配</div>
              <div class="num mt-1 text-[18px] leading-none text-[var(--blue-deep)]">{{ batch.budget_used }}</div>
              <div class="mt-1 text-[10px] text-[var(--ink-faint)]">入选分区</div>
            </div>
            <div class="bg-[var(--paper-2)] px-3 py-2.5">
              <div class="label">预算剩余</div>
              <div class="num mt-1 text-[18px] leading-none">{{ batch.budget_remaining }}</div>
              <div class="mt-1 text-[10px] text-[var(--ink-faint)]">无合格候选时为正</div>
            </div>
            <div class="bg-[var(--paper-2)] px-3 py-2.5">
              <div class="label">单表上限</div>
              <div class="num mt-1 text-[18px] leading-none">{{ batch.max_per_table }}</div>
              <div class="mt-1 text-[10px] text-[var(--ink-faint)]">预算的一半</div>
            </div>
            <div class="bg-[var(--paper-2)] px-3 py-2.5">
              <div class="label">入选表 / 总表</div>
              <div class="num mt-1 text-[18px] leading-none">
                {{ batch.totals.tables_selected }} / {{ batch.totals.tables_total }}
              </div>
              <div class="mt-1 text-[10px] text-[var(--ink-faint)]">跨表覆盖</div>
            </div>
            <div class="bg-[var(--paper-2)] px-3 py-2.5">
              <div class="label">可节省文件</div>
              <div class="num mt-1 text-[18px] leading-none text-[var(--rust)]">{{ batch.totals.saved_files }}</div>
              <div class="mt-1 text-[10px] text-[var(--ink-faint)]">{{ batch.totals.candidate_file_count }} 候选</div>
            </div>
          </div>

          <!-- 预算条：每个入选分区一格，按表着色分组 -->
          <div>
            <div class="label mb-1.5">预算槽位（每格 = 1 个入选分区，颜色区分湖表）</div>
            <div class="flex flex-wrap gap-1">
              <span
                v-for="slot in budgetSlots"
                :key="slot.key"
                class="flex h-7 min-w-[74px] items-center justify-center border px-1.5 text-[10px] font-mono"
                :class="slot.tone"
                :title="`${slot.table} · dt=${slot.partition} · 健康度 ${slot.health} · 省 ${slot.saved} 文件`"
                :data-test="`budget-slot-${slot.rank}`"
              >
                #{{ slot.rank }} {{ slot.partition }}
              </span>
              <span
                v-for="empty in batch.budget_remaining"
                :key="`empty-${empty}`"
                class="flex h-7 min-w-[74px] items-center justify-center border border-dashed border-[var(--rule-soft)] px-1.5 text-[10px] text-[var(--ink-faint)]"
              >
                空置 {{ empty }}
              </span>
            </div>
          </div>

          <div class="grid gap-3 text-[11.5px] md:grid-cols-3">
            <div class="flex items-center justify-between border-b border-dotted border-[var(--rule-soft)] pb-1">
              <span class="text-[var(--ink-soft)]">重写分组 / 重写字节</span>
              <span class="num">{{ batch.totals.rewrite_groups }} 组 · {{ formatBytes(batch.totals.rewritten_bytes) }}</span>
            </div>
            <div class="flex items-center justify-between border-b border-dotted border-[var(--rule-soft)] pb-1">
              <span class="text-[var(--ink-soft)]">预计总耗时</span>
              <span class="num">{{ batch.totals.est_duration_seconds.toFixed(1) }} s</span>
            </div>
            <div class="flex items-center justify-between border-b border-dotted border-[var(--rule-soft)] pb-1">
              <span class="text-[var(--ink-soft)]">预计重写成本</span>
              <span class="num">¥ {{ batch.totals.est_cost_yuan.toFixed(2) }}</span>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <span class="font-mono text-[10px] text-[var(--ink-faint)]">fingerprint {{ batch.fingerprint }}</span>
            <span class="text-[10.5px] text-[var(--ink-soft)]">生成于 {{ batch.created_at }}</span>
            <span v-if="batch.submitted_at" class="text-[10.5px] text-[var(--ink-soft)]">提交于 {{ batch.submitted_at }}</span>
            <button
              v-if="auth.canWrite && canSubmit"
              class="bp-btn bp-btn--solid ml-auto"
              :disabled="store.submitting"
              data-test="submit-orchestration"
              @click="submit"
            >
              {{ store.submitting ? '提交中…' : `提交执行（${batch.totals.tables_selected} 张表）` }}
            </button>
            <span v-else-if="!auth.canWrite" class="leader-note ml-auto">只读角色：可查看编排，不能提交执行。</span>
            <span v-else class="bp-tag ml-auto" :class="batchStatusTagClass(batch.status)">{{ batchLabel(batch.status) }}</span>
          </div>
        </div>
      </section>

      <!-- 每表编排明细 -->
      <section class="mt-6 space-y-5" data-test="table-allocation">
        <div class="flex items-end justify-between border-b border-[var(--rule)] pb-2">
          <span class="label">per-table allocation / 每表入选分区与未入选原因</span>
          <span class="label">{{ batch.items.length }} 张湖表</span>
        </div>

        <article
          v-for="item in batch.items"
          :key="item.id"
          class="rise-2 bp-frame"
          :data-test="`orchestration-item-${item.table_id}`"
        >
          <div class="bp-frame__head">
            <div class="flex flex-wrap items-center gap-2.5">
              <span class="display-sm !text-[15px]">{{ item.table_fq_name }}</span>
              <span class="bp-tag" :class="itemStatusTagClass(item.status)">{{ itemLabel(item.status) }}</span>
              <span v-if="item.rank" class="label">入选顺位 #{{ item.rank }}</span>
            </div>
            <div class="flex items-center gap-3 text-[10.5px] text-[var(--ink-soft)]">
              <span>入选分区 <span class="num">{{ item.selected_partitions.length }}</span></span>
              <span>可节省 <span class="num text-[var(--rust)]">{{ item.metrics.saved_files }}</span> 文件</span>
            </div>
          </div>

          <div class="bp-frame__body space-y-3">
            <!-- 表级未入选原因 -->
            <div
              v-if="item.status === 'skipped' && item.reason_code"
              class="border-l-2 border-[var(--ochre)] bg-[color-mix(in_oklch,var(--ochre)_10%,transparent)] px-3 py-2 text-[11.5px]"
              :data-test="`item-skip-reason-${item.table_id}`"
            >
              <span class="label">未入选原因</span>
              <div class="mt-0.5">{{ reasonLabel(item.reason_code) }}</div>
            </div>

            <!-- 执行链路状态 -->
            <div v-if="item.status !== 'skipped'" class="grid gap-2 text-[11px] md:grid-cols-4">
              <div class="flex items-center justify-between border-b border-dotted border-[var(--rule-soft)] pb-1">
                <span class="text-[var(--ink-soft)]">合并计划</span>
                <RouterLink v-if="item.plan_id" :to="`/jobs`" class="num hover:underline">#{{ item.plan_id }} · {{ item.plan_status }}</RouterLink>
                <span v-else class="num text-[var(--ink-faint)]">待提交</span>
              </div>
              <div class="flex items-center justify-between border-b border-dotted border-[var(--rule-soft)] pb-1">
                <span class="text-[var(--ink-soft)]">执行任务</span>
                <span class="num">{{ item.job_id ? `#${item.job_id} · ${item.job_status} ${item.job_progress ?? 0}%` : '待投递' }}</span>
              </div>
              <div class="flex items-center justify-between border-b border-dotted border-[var(--rule-soft)] pb-1">
                <span class="text-[var(--ink-soft)]">表快照</span>
                <RouterLink
                  v-if="item.snapshot_id"
                  :to="{ path: '/snapshots', query: { table: String(item.table_id) } }"
                  class="num hover:underline"
                >
                  v{{ item.snapshot_version }}
                </RouterLink>
                <span v-else class="num text-[var(--ink-faint)]">完成后生成</span>
              </div>
              <div class="flex items-center justify-between border-b border-dotted border-[var(--rule-soft)] pb-1">
                <span class="text-[var(--ink-soft)]">预计耗时 / 成本</span>
                <span class="num">{{ item.metrics.est_duration_seconds.toFixed(1) }}s · ¥{{ item.metrics.est_cost_yuan.toFixed(2) }}</span>
              </div>
            </div>
            <div v-if="item.failure_reason" class="border-l-2 border-[var(--rust)] bg-[color-mix(in_oklch,var(--rust)_8%,transparent)] px-3 py-2 text-[11px]">
              <span class="label">失败原因</span>
              <div class="mt-0.5">{{ item.failure_reason }}</div>
            </div>

            <!-- 分区决策表 -->
            <div class="overflow-auto">
              <table class="bp-table">
                <thead>
                  <tr>
                    <th>优先级</th>
                    <th>分区</th>
                    <th>健康度</th>
                    <th>文件 / 小文件</th>
                    <th class="text-right">可节省文件</th>
                    <th class="text-right">重写组</th>
                    <th>决策 / 未入选原因</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="decision in sortedDecisions(item)"
                    :key="decision.partition_value"
                    :class="decision.selected ? 'is-selected' : ''"
                    :data-test="`decision-${item.table_id}-${decision.partition_value}`"
                  >
                    <td class="num">{{ decision.rank ?? '—' }}</td>
                    <td>
                      <span class="font-mono text-[11px]">dt={{ decision.partition_value }}</span>
                      <span v-if="decision.risk === 'hot'" class="bp-tag bp-tag--hot ml-2 !text-[8.5px] !px-1">HOT</span>
                    </td>
                    <td>
                      <HealthBadge :band="decision.health_band" :score="decision.health_score" :show-score="true" />
                    </td>
                    <td class="num">{{ decision.file_count }} / {{ decision.small_file_count }}</td>
                    <td class="num" :class="decision.saved_files > 0 ? 'text-[var(--rust)]' : ''">{{ decision.saved_files }}</td>
                    <td class="num">{{ decision.rewrite_groups }}</td>
                    <td>
                      <span v-if="decision.selected" class="bp-tag bp-tag--ok">入选</span>
                      <span v-else class="flex flex-col gap-0.5">
                        <span class="bp-tag" :class="reasonTone(decision.reason_code)">未入选</span>
                        <span class="text-[10.5px] text-[var(--ink-soft)]">{{ reasonLabel(decision.reason_code) }}</span>
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PageHeader from '../components/common/PageHeader.vue'
import ErrorNotice from '../components/common/ErrorNotice.vue'
import HealthBadge from '../components/common/HealthBadge.vue'
import { useOrchestrationStore } from '../stores/orchestrationStore'
import { useAuthStore } from '../stores/authStore'
import { formatBytes } from '../utils/formatBytes'
import type {
  OrchestrationBatch,
  OrchestrationBatchStatus,
  OrchestrationItem,
  OrchestrationItemStatus,
  OrchestrationReasonCode,
  PartitionDecision,
} from '../types/orchestration'
import {
  ORCHESTRATION_BATCH_STATUS_LABELS,
  ORCHESTRATION_ITEM_STATUS_LABELS,
  ORCHESTRATION_REASON_LABELS,
  ACTIVE_BATCH_STATUSES,
  batchStatusTagClass,
  itemStatusTagClass,
} from '../constants/orchestrationStatus'

const store = useOrchestrationStore()
const auth = useAuthStore()
const justGeneratedIdempotent = ref(false)

const batch = computed<OrchestrationBatch | null>(() => store.current)

const canSubmit = computed(() => batch.value !== null && ACTIVE_BATCH_STATUSES.includes(batch.value.status))

const idempotentNotice = computed(() => {
  if (justGeneratedIdempotent.value && batch.value) {
    return '幂等命中：今日编排已存在，未新增编排，预算去向与首次生成完全一致。'
  }
  return ''
})

const TABLE_TONES = [
  'border-[var(--blue)] text-[var(--blue-deep)] bg-[color-mix(in_oklch,var(--blue)_8%,transparent)]',
  'border-[var(--sea)] text-[oklch(38%_0.08_195)] bg-[color-mix(in_oklch,var(--sea)_8%,transparent)]',
  'border-[var(--ochre)] text-[oklch(46%_0.11_70)] bg-[color-mix(in_oklch,var(--ochre)_10%,transparent)]',
  'border-[var(--rust)] text-[var(--rust)] bg-[color-mix(in_oklch,var(--rust)_7%,transparent)]',
]

const budgetSlots = computed(() => {
  if (!batch.value) return []
  const tableTone = new Map<string, string>()
  let toneIndex = 0
  const slots: {
    key: string
    rank: number
    table: string
    partition: string
    health: number
    saved: number
    tone: string
  }[] = []
  for (const item of batch.value.items) {
    if (item.status === 'skipped') continue
    if (!tableTone.has(item.table_fq_name ?? String(item.table_id))) {
      tableTone.set(item.table_fq_name ?? String(item.table_id), TABLE_TONES[toneIndex % TABLE_TONES.length])
      toneIndex += 1
    }
    for (const decision of item.partition_decisions) {
      if (!decision.selected || decision.rank === null) continue
      slots.push({
        key: `${item.table_id}-${decision.partition_value}`,
        rank: decision.rank,
        table: item.table_fq_name ?? '',
        partition: decision.partition_value,
        health: decision.health_score,
        saved: decision.saved_files,
        tone: tableTone.get(item.table_fq_name ?? String(item.table_id)) ?? TABLE_TONES[0],
      })
    }
  }
  return slots.sort((a, b) => a.rank - b.rank)
})

function sortedDecisions(item: OrchestrationItem): PartitionDecision[] {
  return [...item.partition_decisions].sort((a, b) => {
    if (a.selected && b.selected) return (a.rank ?? 0) - (b.rank ?? 0)
    if (a.selected !== b.selected) return a.selected ? -1 : 1
    return a.partition_value.localeCompare(b.partition_value)
  })
}

function batchLabel(status: OrchestrationBatchStatus): string {
  return ORCHESTRATION_BATCH_STATUS_LABELS[status] ?? status
}

function itemLabel(status: OrchestrationItemStatus): string {
  return ORCHESTRATION_ITEM_STATUS_LABELS[status] ?? status
}

function reasonLabel(code: OrchestrationReasonCode | null): string {
  return code ? ORCHESTRATION_REASON_LABELS[code] ?? code : '—'
}

function reasonTone(code: OrchestrationReasonCode | null): string {
  if (code === 'hot_partition_excluded' || code === 'table_paused') return 'bp-tag--hot'
  if (code === 'table_cap_reached' || code === 'budget_exhausted') return 'bp-tag--watch'
  return 'bp-tag--muted'
}

async function generate() {
  try {
    const result = await store.generate({})
    justGeneratedIdempotent.value = result.idempotent
  } catch {
    /* surfaced via store.error */
  }
}

async function submit() {
  if (!batch.value) return
  try {
    await store.submit(batch.value.id)
    // 真实环境 worker 异步执行，进入轮询；eager/演示环境下状态已直接更新
    pollUntilSettled()
  } catch {
    /* surfaced via store.error */
  }
}

let pollTimer: number | null = null
function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}
function pollUntilSettled() {
  if (pollTimer !== null || !batch.value) return
  pollTimer = window.setInterval(async () => {
    if (!batch.value) return
    await store.loadDetail(batch.value.id)
    if (batch.value && !ACTIVE_BATCH_STATUSES.includes(batch.value.status)) stopPolling()
  }, 1500)
}

onBeforeUnmount(stopPolling)

onMounted(async () => {
  await store.loadList()
  if (store.batches.length) store.current = store.batches[0]
})
</script>
