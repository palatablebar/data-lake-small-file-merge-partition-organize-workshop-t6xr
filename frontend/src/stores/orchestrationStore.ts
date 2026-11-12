import { defineStore } from 'pinia'
import {
  fetchOrchestration,
  fetchOrchestrations,
  generateOrchestration,
  submitOrchestration,
} from '../api/orchestration'
import type { OrchestrationBatch } from '../types/orchestration'
import { useAuthStore } from './authStore'

interface OrchestrationState {
  batches: OrchestrationBatch[]
  current: OrchestrationBatch | null
  loading: boolean
  generating: boolean
  submitting: boolean
  error: string
  lastIdempotent: boolean | null
}

export const useOrchestrationStore = defineStore('orchestration', {
  state: (): OrchestrationState => ({
    batches: [],
    current: null,
    loading: false,
    generating: false,
    submitting: false,
    error: '',
    lastIdempotent: null,
  }),
  getters: {
    latest: (state): OrchestrationBatch | null => state.batches[0] ?? state.current,
  },
  actions: {
    async loadList() {
      this.loading = true
      this.error = ''
      try {
        const result = await fetchOrchestrations()
        this.batches = result.items
        if (result.items.length && !this.current) this.current = result.items[0]
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '编排批次加载失败'
      } finally {
        this.loading = false
      }
    },
    async loadDetail(batchId: number) {
      this.loading = true
      this.error = ''
      try {
        const result = await fetchOrchestration(batchId)
        this.current = result.batch
        const index = this.batches.findIndex((item) => item.id === batchId)
        if (index >= 0) this.batches[index] = result.batch
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '编排详情加载失败'
      } finally {
        this.loading = false
      }
    },
    async generate(payload: { rewrite_budget?: number | null; note?: string } = {}) {
      this.generating = true
      this.error = ''
      try {
        const result = await generateOrchestration(payload)
        this.lastIdempotent = result.idempotent
        this.current = result.batch
        const index = this.batches.findIndex((item) => item.id === result.batch.id)
        if (index >= 0) this.batches[index] = result.batch
        else this.batches.unshift(result.batch)
        useAuthStore().setQuota(result.quota)
        return result
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '编排生成失败'
        throw error
      } finally {
        this.generating = false
      }
    },
    async submit(batchId: number) {
      this.submitting = true
      this.error = ''
      try {
        const result = await submitOrchestration(batchId)
        this.current = result.batch
        const index = this.batches.findIndex((item) => item.id === result.batch.id)
        if (index >= 0) this.batches[index] = result.batch
        useAuthStore().setQuota(result.quota)
        return result
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '编排提交失败'
        throw error
      } finally {
        this.submitting = false
      }
    },
  },
})
