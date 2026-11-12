import http from './http'
import type {
  OrchestrationBatch,
  OrchestrationGenerateResult,
  OrchestrationSubmitResult,
} from '../types/orchestration'

export async function generateOrchestration(payload: {
  rewrite_budget?: number | null
  note?: string
} = {}): Promise<OrchestrationGenerateResult> {
  const { data } = await http.post('/orchestrations', payload)
  return data
}

export async function fetchOrchestrations(): Promise<{ items: OrchestrationBatch[] }> {
  const { data } = await http.get('/orchestrations')
  return data
}

export async function fetchOrchestration(batchId: number): Promise<{ batch: OrchestrationBatch }> {
  const { data } = await http.get(`/orchestrations/${batchId}`)
  return data
}

export async function submitOrchestration(batchId: number): Promise<OrchestrationSubmitResult> {
  const { data } = await http.post(`/orchestrations/${batchId}/submit`)
  return data
}
