import type { Alert, CareCircle, CheckIn, CheckInResponse, Evaluation, Senior, Timeline } from './types'
import { accessToken } from './supabase'

const base = (import.meta.env.VITE_API_ORIGIN || '').replace(/\/$/, '')
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await accessToken()
  const url = base ? `${base}${path}` : `/api${path}`
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers }, ...init })
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  return response.json() as Promise<T>
}

export const api = {
  senior: (id: string) => request<Senior>(`/seniors/${id}`),
  // The caregiver view, opened from the link in a Linq text. Everything the
  // SMS deliberately left out lives behind these three calls.
  latestEvaluation: (id: string) => request<Evaluation>(`/seniors/${id}/latest-evaluation`),
  timeline: (id: string) => request<Timeline>(`/seniors/${id}/timeline`),
  circle: (id: string) => request<CareCircle>(`/circle/${id}`),
  acknowledge: (alertId: string, caregiverId: string) =>
    request<Alert>(`/circle/alerts/${alertId}/ack`, { method: 'POST', body: JSON.stringify({ caregiver_id: caregiverId }) }),
  alerts: (id: string) => request<Alert[]>(`/circle/${id}/alerts`),
  checkins: (id: string) => request<CheckIn[]>(`/seniors/${id}/checkins`),
  createCheckIn: (payload: { senior_id: string; text: string; language: string; source: string }) =>
    request<CheckInResponse>('/checkins', { method: 'POST', body: JSON.stringify(payload) }),
}
