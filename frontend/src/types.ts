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
export interface HandoffPacket {
  id: string; senior_id: string; created_at: string; language: string;
  patient_summary_en: string; patient_summary_translated: string;
  presenting_complaint: string; level: ActionLevel;
  red_flags: { code: string; label: string; matched_on?: string[] }[];
  medications: Medication[]; allergies: string[]; conditions: string[];
  recent_checkins: CheckIn[]; disclaimer: string;
}
export interface ReminderResponse { ok: boolean; mocked: boolean; kind: string; recipient?: string; recipients?: string[]; message_id?: string; body: string; reply_expected: string; error?: string }

// -- Caregiver view (opened from the link in a Linq text) -------------------
export interface TimelineEntry { at: string; type: string; level?: ActionLevel; summary: string; detail?: Record<string, unknown> }
export interface BaselineSummary { window_days: number; checkin_count: number; mean_level: number; trending_up: string[]; trending_down: string[] }
export interface Timeline { senior_id: string; entries: TimelineEntry[]; baseline: BaselineSummary }
// Names and roles only -- the caregiver payload carries no phone numbers, so
// a forwarded link cannot hand out the family's contact details.
export interface CircleMember { name: string; role: 'patient' | 'caregiver' | 'care_team' }
export interface OpenAlert { id: string; level: ActionLevel; created_at: string; escalations: number; acknowledged: boolean }
export interface CaregiverView {
  senior: { id: string; display_name: string; age: number; preferred_language: string }
  evaluation: Evaluation | null
  baseline: BaselineSummary
  timeline: TimelineEntry[]
  circle: { members: CircleMember[] }
  open_alert: OpenAlert | null
  acknowledged: boolean
}
export interface AlertAck { caregiver_id: string; at: string; via: string }
export interface Alert {
  id: string; senior_id: string; level: ActionLevel; created_at: string; body: string
  notified: string[]; pending_order: string[]; acks: AlertAck[]; escalations: number; resolved: boolean
}
