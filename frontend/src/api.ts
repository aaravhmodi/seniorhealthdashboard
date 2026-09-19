import type { CheckIn, CheckInResponse, Senior } from './types'
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
  checkins: (id: string) => request<CheckIn[]>(`/seniors/${id}/checkins`),
  createCheckIn: (payload: { senior_id: string; text: string; language: string; source: string }) =>
    request<CheckInResponse>('/checkins', { method: 'POST', body: JSON.stringify(payload) }),
}
