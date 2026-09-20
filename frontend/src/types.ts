export type ActionLevel = 1 | 2 | 3 | 4

export interface Medication { id: string; name: string; dose?: string; schedule?: string }
export interface Caregiver { id: string; name: string; relationship: string; phone_e164: string }
export interface Appointment { id: string; title: string; date?: string; location?: string; notes?: string }
export interface CarePlan { routines: string[]; instructions: string[]; appointments: Appointment[] }
export interface Senior {
  id: string; display_name: string; date_of_birth: string; age: number; preferred_language: string
  gender?: string; phone_e164?: string; consent?: Record<string, boolean>; conditions: string[]; allergies: string[]; medications: Medication[]; caregivers?: Caregiver[]
}
export interface Symptom { label: string; severity?: number; onset?: string; is_new?: boolean }
export interface CheckIn { id: string; created_at: string; raw_text?: string; symptoms: Symptom[]; source: string }
export interface Evaluation {
  level: ActionLevel; level_label: string; explanation: string; explanation_en?: string
  recommended_actions: string[]; confidence: number; red_flags: { label: string; matched_on?: string[]; forces_level?: ActionLevel }[]
  evidence?: EvidenceCard[]; requires_human_review?: boolean; model_risk?: number
}
export interface EvidenceCard {
  kind: string; title: string; detail: string; source: string; weight?: number
  stat?: { value: number; unit: string; n?: number; ci_low?: number; ci_high?: number }
}
export interface CheckInResponse { checkin: CheckIn; evaluation: Evaluation }

// -- Caregiver view (opened from the link in a Linq text) -------------------
export interface TimelineEntry { at: string; type: string; level?: ActionLevel; summary: string; detail?: Record<string, unknown> }
export interface BaselineSummary { window_days: number; checkin_count: number; mean_level: number; trending_up: string[]; trending_down: string[] }
export interface Timeline { senior_id: string; entries: TimelineEntry[]; baseline: BaselineSummary }
export interface CircleMember { handle: string; name: string; role: 'patient' | 'caregiver' | 'care_team'; caregiver_id?: string }
export interface CareCircle { senior_id: string; chat_id?: string; group_name: string; members: CircleMember[]; status: string; mocked: boolean }
export interface AlertAck { caregiver_id: string; at: string; via: string }
export interface Alert {
  id: string; senior_id: string; level: ActionLevel; created_at: string; body: string
  notified: string[]; pending_order: string[]; acks: AlertAck[]; escalations: number; resolved: boolean
}
