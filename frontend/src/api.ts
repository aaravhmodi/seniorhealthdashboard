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
  transcribe: async (audio: Blob, language: string) => {
    const form = new FormData()
    form.append('file', audio, 'check-in.webm')
    form.append('language', language)
    const response = await fetch(`${base || '/api'}/voice/transcribe`, { method: 'POST', body: form })
    if (!response.ok) throw new Error(`Transcription failed (${response.status})`)
    return response.json() as Promise<{ text: string; confidence: number; language: string; low_confidence: boolean }>
  },
}
