export type ActionLevel = 1 | 2 | 3 | 4

export interface Medication { id: string; name: string; dose?: string; schedule?: string }
export interface Caregiver { id: string; name: string; relationship: string; phone_e164: string }
export interface Appointment { id: string; title: string; date?: string; location?: string; notes?: string }
export interface CarePlan { routines: string[]; instructions: string[]; appointments: Appointment[] }
export interface Senior {
  id: string; display_name: string; date_of_birth: string; age: number; preferred_language: string
  phone_e164?: string; consent?: Record<string, boolean>; conditions: string[]; allergies: string[]; medications: Medication[]; caregivers?: Caregiver[]
}
export interface Symptom { label: string; severity?: number; onset?: string; is_new?: boolean }
export interface CheckIn { id: string; created_at: string; raw_text?: string; symptoms: Symptom[]; source: string }
export interface Evaluation {
  level: ActionLevel; level_label: string; explanation: string; explanation_en?: string
  recommended_actions: string[]; confidence: number; red_flags: { label: string }[]
}
export interface CheckInResponse { checkin: CheckIn; evaluation: Evaluation }
