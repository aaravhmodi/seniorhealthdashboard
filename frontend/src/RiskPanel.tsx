import { useState } from "react";
import { ChevronDown } from "lucide-react";
import type { DatasetNote, RiskAssessment, RiskConcern, RiskDriver } from "./types";

// -- What this could be ----------------------------------------------------
// The dashboard used to say "go to the emergency department" without saying
// what it thought this was or how sure it was. This panel answers both, and
// ties each percentage to the action that percentage earns.
//
// One rule holds the whole thing together: every bar is drawn on the SAME
// 0-100 axis as the band strip at the top, so where a concern lands is
// visibly why it got the action it got. Nothing here is a decorative meter.

const BAND_TONE: Record<string, string> = {
  monitor: "calm", today: "watch", emergency: "urgent", now: "critical",
};

function DriverRow({ driver, widest }: { driver: RiskDriver; widest: number }) {
  const magnitude = Math.abs(driver.delta_points);
  return (
    <div className={`driver-row ${driver.delta_points < 0 ? "lowers" : ""}`}>
      <div className="driver-head">
        <span className="driver-label">{driver.label}</span>
        <strong className="driver-delta">
          {driver.delta_points >= 0 ? "+" : "\u2212"}{magnitude.toFixed(1)} pts
        </strong>
      </div>
      <div className="driver-track" aria-hidden="true">
        <div style={{ width: `${widest ? (magnitude / widest) * 100 : 0}%` }} />
      </div>
      <p className="driver-detail">{driver.detail}</p>
      <p className="driver-source">
        <span className={`driver-badge ${driver.fitted ? "fitted" : "chosen"}`}>
          {driver.fitted ? "measured" : "clinical weighting"}
        </span>
        {driver.source}
      </p>
    </div>
  );
}

function ConcernCard({ concern, open, onToggle, audience }: {
  concern: RiskConcern; open: boolean; onToggle: () => void;
  audience: "patient" | "caregiver";
}) {
  const widest = Math.max(1, ...concern.drivers.map((driver) => Math.abs(driver.delta_points)));
  const detailId = `concern-why-${concern.code}`;
  return (
    <article className={`concern-card ${BAND_TONE[concern.band] || "calm"}`}>
      <div className="concern-top">
        <div className="concern-name">
          <h4>{concern.label}</h4>
          <p>{concern.plain}</p>
        </div>
        <div className="concern-figure">
          <strong>{concern.probability_percent.toFixed(0)}<small>%</small></strong>
          <span>{concern.band_label}</span>
        </div>
      </div>
      <div className="concern-meter" aria-hidden="true">
        <div className="concern-meter-fill" style={{ width: `${concern.probability_percent}%` }} />
        <div className="concern-meter-base" style={{ left: `${concern.base_rate_percent}%` }} />
      </div>
      <p className="concern-scale-note">
        Starts at {concern.base_rate_percent.toFixed(0)}% for someone {audience === "patient" ? "your" : "their"} age with this complaint
        {concern.drivers.length > 0 && <>, then moves to {concern.probability_percent.toFixed(0)}% on what {audience === "patient" ? "you" : "they"} told us</>}
      </p>
      <p className="concern-action">
        <strong>At {concern.probability_percent.toFixed(0)}%:</strong> {concern.action}
      </p>
      <button
        type="button"
        className="concern-toggle"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={onToggle}
      >
        <ChevronDown size={16} className={open ? "rotated" : ""} />
        {open ? "Hide the working" : "Why this number?"}
      </button>
      {open && (
        <div className="concern-why" id={detailId}>
          <div className="why-block">
            <h5>What raised it at all</h5>
            <ul className="matched-list">
              {concern.matched_on.map((match) => <li key={match}>{match}</li>)}
            </ul>
          </div>
          <div className="why-block">
            <h5>Where the number started</h5>
            <p>{concern.base_rate_detail}</p>
          </div>
          {concern.drivers.length > 0 && (
            <div className="why-block">
              <h5>What moved it, and by how much</h5>
              <div className="driver-list">
                {concern.drivers.map((driver) => (
                  <DriverRow key={driver.label} driver={driver} widest={widest} />
                ))}
              </div>
              <p className="driver-caveat">
                Each figure is this check-in&rsquo;s percentage with that piece of
                evidence minus the percentage without it. They are not slices of a
                pie, so they do not add up to the total.
              </p>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export function RiskPanel({ risk, audience = "patient" }: {
  risk: RiskAssessment;
  audience?: "patient" | "caregiver";
}) {
  const [openCode, setOpenCode] = useState<string | null>(risk.top_concern ?? null);
  const [showModel, setShowModel] = useState(false);
  const [showSources, setShowSources] = useState(false);
  if (!risk.concerns.length) {
    return (
      <section className="risk-panel risk-panel-empty" aria-labelledby="risk-title-empty">
        <p className="eyebrow">What this could be</p>
        <h2 id="risk-title-empty">No specific concern was triggered</h2>
        <p className="risk-sub">
          Nothing in this check-in crossed a named screening threshold.
          Keep watching, and answer the follow-up question above so the next check-in
          can become more specific.
        </p>
      </section>
    );
  }
  const top = risk.concerns[0];
  const others = risk.concerns.length - 1;

  return (
    <section className="risk-panel" aria-labelledby="risk-title">
      <div className="risk-heading">
        <div>
          <p className="eyebrow">What this could be</p>
          <h2 id="risk-title">
            {top.label}
            {others > 0 && <span className="risk-others">, and {others} other thing{others === 1 ? "" : "s"} worth naming</span>}
          </h2>
        <p className="risk-sub">
            Each percentage is how often a presentation like {audience === "patient" ? "yours" : "this one"} ended in
            hospital rather than being sent home, adjusted for {audience === "patient" ? "your" : "their"} age, {audience === "patient" ? "your" : "their"}
            medicines and what {audience === "patient" ? "you" : "they"} told us today.
          </p>
        </div>
        <div className={`risk-headline ${BAND_TONE[top.band] || "calm"}`}>
          <strong>{top.probability_percent.toFixed(0)}<small>%</small></strong>
          <span>{top.band_label}</span>
        </div>
      </div>

      <div className="band-strip">
        <div className="band-track">
          {risk.bands.map((band) => (
            <div
              key={band.band}
              className={`band-zone ${BAND_TONE[band.band]}`}
              style={{ width: `${band.upper_percent - band.lower_percent}%` }}
            >
              <span>{band.label}</span>
            </div>
          ))}
          <div className="band-marker" style={{ left: `${top.probability_percent}%` }} aria-hidden="true" />
        </div>
        <div className="band-ticks">
          {risk.bands.map((band) => (
            <span key={band.band} style={{ left: `${band.lower_percent}%` }}>{band.lower_percent}%</span>
          ))}
          <span style={{ left: "100%" }}>100%</span>
        </div>
      </div>

      <div className="concern-list">
        {risk.concerns.map((concern) => (
          <ConcernCard
            key={concern.code}
            concern={concern}
            open={openCode === concern.code}
            audience={audience}
            onToggle={() => setOpenCode(openCode === concern.code ? null : concern.code)}
          />
        ))}
      </div>

      <div className="risk-provenance">
        <button
          type="button"
          className="concern-toggle"
          aria-expanded={showSources}
          onClick={() => setShowSources(!showSources)}
        >
          <ChevronDown size={16} className={showSources ? "rotated" : ""} />
          Where these numbers come from
        </button>
        {showSources && (
          <ul className="source-list">
            {risk.datasets.map((dataset: DatasetNote) => (
              <li key={dataset.name} className={dataset.loaded ? "" : "not-loaded"}>
                <div className="source-head">
                  <strong>{dataset.name}</strong>
                  <span className="source-role">{dataset.role}</span>
                  {dataset.trained && <span className="source-tag trained">model trained on this</span>}
                  {!dataset.loaded && <span className="source-tag missing">not loaded here</span>}
                </div>
                <p>{dataset.detail}</p>
              </li>
            ))}
          </ul>
        )}
        {risk.model_risk_percent !== undefined && risk.model_risk_percent !== null && (
          <p className="model-headline">
            The trained model reads this check-in at{" "}
            <strong>{risk.model_risk_percent.toFixed(0)}%</strong> overall risk of
            needing admission or observation.
          </p>
        )}
        <button
          type="button"
          className="concern-toggle"
          aria-expanded={showModel}
          onClick={() => setShowModel(!showModel)}
        >
          <ChevronDown size={16} className={showModel ? "rotated" : ""} />
          How that model was trained
        </button>
        {showModel && (
          <div className="model-detail">
            <p>{risk.model_basis}</p>
            <div className="model-stats">
              {risk.model_n ? <div><span>Training records</span><strong>{risk.model_n.toLocaleString()}</strong></div> : null}
              {risk.model_years.length ? <div><span>Years</span><strong>{risk.model_years.join(", ")}</strong></div> : null}
              {risk.model_auc ? <div><span>Held-out AUC</span><strong>{risk.model_auc.toFixed(3)}</strong></div> : null}
              {risk.model_holdout_years.length ? <div><span>Tested on</span><strong>{risk.model_holdout_years.join(", ")}</strong></div> : null}
            </div>
            {risk.model_tokens.length > 0 && (
              <>
          <h5>What it keyed on in {audience === "patient" ? "your" : "their"} words</h5>
                <ul className="token-list">
                  {risk.model_tokens.map((token) => <li key={token}>{token}</li>)}
                </ul>
                <p className="driver-caveat">
                  These are the terms with the largest positive contribution to
                  this score, read straight off the fitted model &mdash; not a
                  guess about what it might have used.
                </p>
              </>
            )}
          </div>
        )}
        <p className="risk-ladder-note">{risk.ladder_note}</p>
      </div>
    </section>
  );
}
