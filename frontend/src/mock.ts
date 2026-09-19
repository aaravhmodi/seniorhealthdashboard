import type { CarePlan, CheckIn, Evaluation, Senior } from './types'

export const demoSenior: Senior = {
  id: 'senior_00001', display_name: 'Maria Santos', date_of_birth: '1947-03-08', age: 79, preferred_language: 'es',
  conditions: ['High blood pressure', 'Type 2 diabetes'], allergies: ['Penicillin'],
  medications: [
    { id: 'm1', name: 'Metformin', dose: '500 mg', schedule: 'With breakfast and dinner' },
    { id: 'm2', name: 'Lisinopril', dose: '10 mg', schedule: 'Every morning' },
  ],
}
export const demoCheckins: CheckIn[] = [
  { id: 'c1', created_at: 'Today, 9:15 AM', source: 'voice', raw_text: 'I felt a little dizzy when I stood up this morning.', symptoms: [{ label: 'Dizziness', severity: 3, onset: 'This morning', is_new: true }] },
  { id: 'c2', created_at: 'Sep 16, 6:40 PM', source: 'text', raw_text: 'My knees hurt after the walk, but I am resting now.', symptoms: [{ label: 'Knee pain', severity: 4, onset: 'After walking' }] },
  { id: 'c3', created_at: 'Sep 12, 10:20 AM', source: 'voice', raw_text: 'No new concerns today.', symptoms: [] },
]
export const demoEvaluation: Evaluation = {
  level: 2, level_label: 'Call your clinic today', confidence: .82,
  explanation: 'Because this dizziness is new and you take blood pressure medicine, it is safest to call your clinic today. Sit down if you feel dizzy, and stand up slowly.',
  explanation_en: 'New dizziness in a patient taking blood-pressure medication. Advise same-day clinician or pharmacist contact.',
  recommended_actions: ['Call your clinic or pharmacist today', 'Sit down if you feel dizzy', 'Stand up slowly and drink water unless on fluid restriction'],
  red_flags: [],
}
export const demoCarePlan: CarePlan = {
  routines: ['Follow a lower-salt meal plan', 'Drink water throughout the day', 'Take a short walk after lunch when comfortable'],
  instructions: ['Check blood sugar before breakfast', 'Stand up slowly if you feel dizzy', 'Use your walker when leaving home'],
  appointments: [{ id: 'a1', title: 'Cardiology follow-up', date: 'October 2 at 10:00 AM', location: 'Riverside Clinic · Suite 210', notes: 'Bring your blood-pressure log.' }],
}
