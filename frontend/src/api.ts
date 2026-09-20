import type { CaregiverView, CheckIn, CheckInResponse, HandoffPacket, NotificationReceipt, ReminderJob, ReminderResponse, Senior } from './types'
import { accessToken } from './supabase'

const base = (import.meta.env.VITE_API_ORIGIN || '').replace(/\/$/, '')
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await accessToken()
  const url = base ? `${base}${path}` : `/api${path}`
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers }, ...init })
  if (!response.ok) {
    let detail = ""
    try {
      const payload = await response.json() as { detail?: string }
      detail = typeof payload.detail === "string" ? `: ${payload.detail}` : ""
    } catch {
      // Preserve the status-only fallback for non-JSON proxy errors.
    }
    throw new Error(`Request failed (${response.status})${detail}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  createSenior: (senior: Senior) => request<Senior>('/seniors', { method: 'POST', body: JSON.stringify(senior) }),
  updateSenior: (senior: Senior) => request<Senior>(`/seniors/${senior.id}`, { method: 'PUT', body: JSON.stringify(senior) }),
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
  shareCheckinWithCaregiver: (checkin_id: string) => request<NotificationReceipt>(`/checkins/${checkin_id}/caregiver`, { method: 'POST' }),
  createCheckIn: (payload: { senior_id: string; text: string; language: string; source: string; follow_up_for_id?: string }) =>
    request<CheckInResponse>('/checkins', { method: 'POST', body: JSON.stringify(payload) }),
  handoff: (id: string) => request<HandoffPacket>(`/handoff/${id}`),
  sendReminder: (senior_id: string, kind: string, language: string, recipient: 'self' | 'caregiver' | 'both') =>
    request<ReminderResponse>('/reminders/send', { method: 'POST', body: JSON.stringify({ senior_id, kind, language, recipient }) }),
  reminders: (senior_id: string) => request<ReminderJob[]>(`/seniors/${senior_id}/reminders`),
  scheduleReminder: (payload: { senior_id: string; kind: string; language: string; recipient: 'self' | 'caregiver' | 'both'; scheduled_for: string }) =>
    request<ReminderJob>(`/seniors/${payload.senior_id}/reminders`, { method: 'POST', body: JSON.stringify(payload) }),
  cancelReminder: (senior_id: string, reminder_id: string) =>
    request<ReminderJob>(`/seniors/${senior_id}/reminders/${reminder_id}`, { method: 'DELETE' }),
}
