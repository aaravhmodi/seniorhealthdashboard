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
import type { Alert, CareCircle, Evaluation, Senior, Timeline } from './types'

const LEVEL_TONE: Record<number, { label: string; className: string }> = {
  1: { label: 'Steady', className: 'level-1' },
  2: { label: 'Call the clinic today', className: 'level-2' },
  3: { label: 'Needs the emergency department', className: 'level-3' },
  4: { label: 'Emergency help now', className: 'level-4' },
}

/** `/c/sen_rosa?ev=eval_00051` -> the ids, or null when this is not that route. */
export function caregiverRoute(pathname: string, search: string) {
  const match = /^\/c\/([A-Za-z0-9_-]+)\/?$/.exec(pathname)
  if (!match) return null
  return { seniorId: match[1], evaluationId: new URLSearchParams(search).get('ev') || undefined }
}

export default function Caregiver({ seniorId }: { seniorId: string; evaluationId?: string }) {
  const [senior, setSenior] = useState<Senior | null>(null)
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [circle, setCircle] = useState<CareCircle | null>(null)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    void (async () => {
      try {
        const person = await api.senior(seniorId)
        if (!live) return
        setSenior(person)
      } catch {
        // A wrong or expired link must say so plainly rather than render an
        // empty page that looks like "nothing is wrong".
        if (live) setError('This link is not valid. Please contact the care team.')
        return
      }
      // The rest is best-effort: a senior with no evaluation yet is a real
      // state, not an error.
      const settle = <T,>(p: Promise<T>) => p.catch(() => null)
      const [ev, tl, cl, al] = await Promise.all([
        settle(api.latestEvaluation(seniorId)), settle(api.timeline(seniorId)),
        settle(api.circle(seniorId)), settle(api.alerts(seniorId)),
      ])
      if (!live) return
      setEvaluation(ev); setTimeline(tl); setCircle(cl); setAlerts(al || [])
    })()
    return () => { live = false }
  }, [seniorId])

  const openAlert = alerts.find(a => !a.resolved)
  const me = circle?.members.find(m => m.role === 'caregiver')

  const acknowledge = async () => {
    if (!openAlert || !me?.caregiver_id || busy) return
    setBusy(true)
    try {
      const updated = await api.acknowledge(openAlert.id, me.caregiver_id)
      setAlerts(current => current.map(a => (a.id === updated.id ? updated : a)))
    } catch { setError('That could not be saved. Please call the care team.') }
    finally { setBusy(false) }
  }

  if (error) return <main className="caregiver-page"><section className="caregiver-card"><h1>{error}</h1></section></main>
  if (!senior) return <main className="caregiver-page"><section className="caregiver-card"><p className="muted">Loading…</p></section></main>

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

      {openAlert && me?.caregiver_id && (
        <section className="caregiver-card ack-card">
          <p>The care team is waiting to hear that someone has seen this.</p>
          <button className="primary-button big-button" disabled={busy} onClick={() => void acknowledge()}>
            <Check size={20}/> {busy ? 'Saving…' : 'I have seen this'}
          </button>
          {openAlert.escalations > 0 && <p className="muted">Already passed on to {openAlert.escalations + 1} people.</p>}
        </section>
      )}
      {openAlert && openAlert.resolved === false && !me?.caregiver_id && (
        <section className="caregiver-card ack-card"><p className="muted">Reply to the text, or tap back on it, to tell us you have seen this.</p></section>
      )}
      {alerts.some(a => a.resolved) && !openAlert && (
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

      {timeline && timeline.baseline.checkin_count > 0 && (
        <section className="caregiver-card">
          <h2>Compared with their usual</h2>
          <p className="muted">
            {timeline.baseline.checkin_count} check-ins over the last {timeline.baseline.window_days} days.
            {timeline.baseline.trending_up.length > 0 && ` Getting more frequent: ${timeline.baseline.trending_up.join(', ')}.`}
          </p>
        </section>
      )}

      {timeline && timeline.entries.length > 0 && (
        <section className="caregiver-card">
          <h2>Recent activity</h2>
          {timeline.entries.slice(0, 8).map((entry, index) => (
            <p className="compact-item" key={`${entry.at}-${index}`}>
              <strong>{entry.summary}</strong><br/><span className="muted">{new Date(entry.at).toLocaleString()}</span>
            </p>
          ))}
        </section>
      )}

      {circle && circle.members.length > 0 && (
        <section className="caregiver-card">
          <h2><Users size={18}/> Who is in this circle</h2>
          <ul className="action-list">{circle.members.map(m => <li key={m.handle}>{m.name}</li>)}</ul>
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
