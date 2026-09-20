/**
 * The page behind the link in a Linq text.
 *
 * The text message is deliberately thin -- a status word and this link, no
 * symptoms, no medicines, no diagnosis, because SMS and iMessage are not
 * HIPAA-grade. So everything the text had to leave out has to be *here*, or
 * the whole design is a shell: a family that clicks through to nothing
 * learns to stop clicking.
 *
 * It opens cold. The daughter is on a phone, on a sidewalk, having read one
 * sentence that frightened her. So: the level first and large, the one action
 * second, the reasoning under it, and an acknowledge button she can hit
 * without reading further -- the same acknowledgement the ✅ tapback sends,
 * because she may well have opened the link instead of tapping back.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, ArrowRight, Check, HeartPulse, Phone, Users } from 'lucide-react'
import { api } from './api'
import type { CaregiverView } from './types'
import { RiskPanel } from './RiskPanel'

const LEVEL_TONE: Record<number, { label: string; className: string }> = {
  1: { label: 'Steady', className: 'level-1' },
  2: { label: 'Call the clinic today', className: 'level-2' },
  3: { label: 'Needs the emergency department', className: 'level-3' },
  4: { label: 'Emergency help now', className: 'level-4' },
}

/** `/c/<signed token>` -> the token, or null when this is not that route.
 *
 * The path used to carry the senior's id, which made the link a guess rather
 * than a credential. It now carries a signed, expiring token; this only has to
 * recognise the shape and hand it to the API, which does the verifying. */
export function caregiverRoute(pathname: string) {
  const match = /^\/c\/([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\/?$/.exec(pathname)
  return match ? { token: match[1] } : null
}

export default function Caregiver({ token }: { token: string }) {
  const [view, setView] = useState<CaregiverView | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    void api.caregiverView(token)
      .then(data => { if (live) setView(data) })
      // A refused or expired link says so plainly. Rendering an empty page
      // would read as "nothing is wrong", which is the one wrong answer.
      .catch(() => { if (live) setError('This link has expired or is not valid. Please contact the care team.') })
    return () => { live = false }
  }, [token])

  const acknowledge = async () => {
    if (busy) return
    setBusy(true)
    try {
      await api.caregiverAck(token)
      const fresh = await api.caregiverView(token)
      setView(fresh)
    } catch { setError('That could not be saved. Please call the care team.') }
    finally { setBusy(false) }
  }

  if (error) return <main className="caregiver-page"><section className="caregiver-card"><h1>{error}</h1></section></main>
  if (!view) return <main className="caregiver-page"><section className="caregiver-card"><p className="muted">Loading…</p></section></main>

  const { senior, evaluation, baseline, timeline, circle, open_alert: openAlert } = view
  const level = evaluation?.level ?? 1
  const tone = LEVEL_TONE[level]
  const firstName = senior.display_name.split(' ')[0]

  return (
    <main className="caregiver-page">
      <header className="caregiver-head">
        <div className="brand"><span className="brand-mark"><HeartPulse size={22}/></span>carepath</div>
        <span className="muted">Care update for {senior.display_name}</span>
      </header>

      <section className={`caregiver-card status-card ${tone.className}`}>
        <span className="status-eyebrow">{evaluation ? 'Latest check-in' : 'No check-in yet'}</span>
        <h1>{tone.label}</h1>
        {evaluation
          ? <p className="status-explain">{evaluation.explanation_en || evaluation.explanation}</p>
          : <p className="status-explain">{firstName} has not checked in with us yet. There is nothing new to report.</p>}
        {level >= 3 && <p className="status-urgent"><AlertTriangle size={18}/> If this is an emergency, call 911.</p>}
      </section>

      {openAlert && !openAlert.acknowledged && (
        <section className="caregiver-card ack-card">
          <p>The care team is waiting to hear that someone has seen this.</p>
          <button className="primary-button big-button" disabled={busy} onClick={() => void acknowledge()}>
            <Check size={20}/> {busy ? 'Saving…' : 'I have seen this'}
          </button>
          {openAlert.escalations > 0 && <p className="muted">Already passed on to {openAlert.escalations + 1} people.</p>}
        </section>
      )}
      {!openAlert && view.acknowledged && (
        <section className="caregiver-card ack-card"><p><Check size={18}/> Someone has acknowledged the latest alert.</p></section>
      )}

      {evaluation && evaluation.recommended_actions.length > 0 && (
        <section className="caregiver-card">
          <h2>What to do</h2>
          <ul className="action-list">{evaluation.recommended_actions.map(a => <li key={a}><ArrowRight size={16}/> {a}</li>)}</ul>
        </section>
      )}

      {evaluation && evaluation.red_flags.length > 0 && (
        <section className="caregiver-card">
          <h2>Why now</h2>
          <ul className="action-list">{evaluation.red_flags.map(f => <li key={f.label}><AlertTriangle size={16}/> {f.label}</li>)}</ul>
        </section>
      )}

      {/* The question a family actually asks after "what do I do" is "what do
          you think this IS". The same panel the patient sees, on the same
          numbers -- a caregiver who is told to drive to a hospital deserves
          the reasoning, not a level and a verb. */}
      {evaluation?.risk && (
        <RiskPanel risk={evaluation.risk} audience="caregiver" />
      )}

      {baseline.checkin_count > 0 && (
        <section className="caregiver-card">
          <h2>Compared with their usual</h2>
          <p className="muted">
            {baseline.checkin_count} check-ins over the last {baseline.window_days} days.
            {baseline.trending_up.length > 0 && ` Getting more frequent: ${baseline.trending_up.join(', ')}.`}
          </p>
        </section>
      )}

      {timeline.length > 0 && (
        <section className="caregiver-card">
          <h2>Recent activity</h2>
          {timeline.map((entry, index) => (
            <p className="compact-item" key={`${entry.at}-${index}`}>
              <strong>{entry.summary}</strong><br/><span className="muted">{new Date(entry.at).toLocaleString()}</span>
            </p>
          ))}
        </section>
      )}

      {circle.members.length > 0 && (
        <section className="caregiver-card">
          <h2><Users size={18}/> Who is in this circle</h2>
          <ul className="action-list">{circle.members.map(m => <li key={m.name}>{m.name}</li>)}</ul>
        </section>
      )}

      <footer className="caregiver-foot">
        <p className="muted">
          <Phone size={15}/> Decision support only. A nurse reviews anything urgent.
          Details are shown here, and never in a text message, because texting is not a private channel.
        </p>
      </footer>
    </main>
  )
}
