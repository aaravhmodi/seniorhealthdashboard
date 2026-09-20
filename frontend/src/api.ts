import type { CaregiverView, CheckIn, CheckInResponse, HandoffPacket, Senior } from './types'
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
  createSenior: (senior: Senior) => request<Senior>('/seniors', { method: 'POST', body: JSON.stringify(senior) }),
  senior: (id: string) => request<Senior>(`/seniors/${id}`),
  // The caregiver view, opened from the link in a Linq text. Everything the
  // SMS deliberately left out lives behind these three calls.
  // The caregiver page authenticates with the signed link itself, not a
  // session: whoever opens it has never signed in and never will. One call,
  // because each extra endpoint is another chance to get the check wrong.
  caregiverView: (token: string) => request<CaregiverView>(`/caregiver/${token}`),
  caregiverAck: (token: string) => request<{ acknowledged: boolean }>(`/caregiver/${token}/ack`, { method: 'POST' }),
  checkins: (id: string) => request<CheckIn[]>(`/seniors/${id}/checkins`),
  checkin: (id: string, language: string) => request<CheckInResponse>(`/checkins/${id}?language=${encodeURIComponent(language)}`),
  createCheckIn: (payload: { senior_id: string; text: string; language: string; source: string }) =>
    request<CheckInResponse>('/checkins', { method: 'POST', body: JSON.stringify(payload) }),
  handoff: (id: string) => request<HandoffPacket>(`/handoff/${id}`),
}
