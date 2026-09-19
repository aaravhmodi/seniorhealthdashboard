import type { CheckIn, CheckInResponse, Senior } from './types'

const base = import.meta.env.VITE_API_ORIGIN || ''
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const origin = base || '/api'
  const response = await fetch(`${origin}${path}`, { headers: { 'Content-Type': 'application/json', ...init?.headers }, ...init })
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  return response.json() as Promise<T>
}

export const api = {
  senior: (id: string) => request<Senior>(`/seniors/${id}`),
  checkins: (id: string) => request<CheckIn[]>(`/seniors/${id}/checkins`),
  createCheckIn: (payload: { senior_id: string; text: string; language: string; source: string }) =>
    request<CheckInResponse>('/checkins', { method: 'POST', body: JSON.stringify(payload) }),
}
