import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import type { DatasetNote, RiskAssessment, RiskConcern, RiskDriver } from "./types";
import { supportedLanguage } from "./i18n";
import { getRiskCopy, type RiskCopy } from "./riskCopy";

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

function DriverRow({ driver, widest, copy }: { driver: RiskDriver; widest: number; copy: RiskCopy }) {
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
          {driver.fitted ? copy.measured : copy.clinicalWeighting}
        </span>
        {driver.source}
      </p>
    </div>
  );
}

function ConcernCard({ concern, open, onToggle, audience, copy }: {
  concern: RiskConcern; open: boolean; onToggle: () => void;
  audience: "patient" | "caregiver"; copy: RiskCopy;
}) {
  const widest = Math.max(1, ...concern.drivers.map((driver) => Math.abs(driver.delta_points)));
  const detailId = `concern-why-${concern.code}`;
  return (
    <article className={`concern-card ${BAND_TONE[concern.band] || "calm"}`}>
      <div className="concern-top">
        <div className="concern-name">
          <h4>{copy.concernLabels[concern.code] || concern.label}</h4>
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
        {copy.startsAt} {concern.base_rate_percent.toFixed(0)}% {audience === "patient" ? "for someone your age" : "for someone their age"} {copy.withComplaint}
        {concern.drivers.length > 0 && <>; {copy.thenMoves} {concern.probability_percent.toFixed(0)}% {copy.onWhat} {audience === "patient" ? "you" : "they"} told us</>}
      </p>
      <p className="concern-action">
        <strong>{copy.at} {concern.probability_percent.toFixed(0)}%:</strong> {copy.bandActions[concern.band] || concern.action}
      </p>
      <button
        type="button"
        className="concern-toggle"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={onToggle}
      >
        <ChevronDown size={16} className={open ? "rotated" : ""} />
        {open ? copy.hideWorking : copy.whyNumber}
      </button>
      {open && (
        <div className="concern-why" id={detailId}>
          <div className="why-block">
            <h5>{copy.whatRaised}</h5>
            <ul className="matched-list">
              {concern.matched_on.map((match) => <li key={match}>{match}</li>)}
            </ul>
          </div>
          <div className="why-block">
            <h5>{copy.whereStarted}</h5>
            <p>{concern.base_rate_detail}</p>
          </div>
          {concern.drivers.length > 0 && (
            <div className="why-block">
              <h5>{copy.whatMoved}</h5>
              <div className="driver-list">
                {concern.drivers.map((driver) => (
                  <DriverRow key={driver.label} driver={driver} widest={widest} copy={copy} />
                ))}
              </div>
              <p className="driver-caveat">
                {copy.driverCaveat}
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
  const { i18n } = useTranslation();
  const copy = getRiskCopy(supportedLanguage(i18n.language));
  const [openCode, setOpenCode] = useState<string | null>(risk.top_concern ?? null);
  const [showModel, setShowModel] = useState(false);
  const [showSources, setShowSources] = useState(false);
  if (!risk.concerns.length) {
    return (
      <section className="risk-panel risk-panel-empty" aria-labelledby="risk-title-empty">
        <p className="eyebrow">{copy.whatCouldBe}</p>
        <h2 id="risk-title-empty">{copy.noConcern}</h2>
        <p className="risk-sub">
          {copy.noConcernText}
        </p>
      </section>
    );
  }
  const top = risk.concerns[0];
  const others = risk.concerns.length - 1;

  const explanation = copy.riskExplanation
    .replace("{presentation}", audience === "patient" ? "yours" : "this one")
    .replace("{possessive}", audience === "patient" ? "your" : "their")
    .replace("{possessive}", audience === "patient" ? "your" : "their")
    .replace("{subject}", audience === "patient" ? "you" : "they");
  const topLabel = copy.concernLabels[top.code] || top.label;

  return (
    <section className="risk-panel" aria-labelledby="risk-title">
      <div className="risk-heading">
        <div>
          <p className="eyebrow">{copy.whatCouldBe}</p>
          <h2 id="risk-title">
            {topLabel}
            {others > 0 && <span className="risk-others">{copy.otherThing(others)}</span>}
          </h2>
          <p className="risk-sub">{explanation}</p>
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
              <span>{copy.bandLabels[band.band] || band.label}</span>
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
            copy={copy}
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
          {copy.whereNumbers}
        </button>
        {showSources && (
          <ul className="source-list">
            {risk.datasets.map((dataset: DatasetNote) => (
              <li key={dataset.name} className={dataset.loaded ? "" : "not-loaded"}>
                <div className="source-head">
                  <strong>{dataset.name}</strong>
                  <span className="source-role">{dataset.role}</span>
                  {dataset.trained && <span className="source-tag trained">{copy.trainedModel}</span>}
                  {!dataset.loaded && <span className="source-tag missing">{copy.notLoaded}</span>}
                </div>
                <p>{dataset.detail}</p>
              </li>
            ))}
          </ul>
        )}
        {risk.model_risk_percent !== undefined && risk.model_risk_percent !== null && (
          <p className="model-headline">
            {copy.modelReads}{" "}
            <strong>{risk.model_risk_percent.toFixed(0)}%</strong> overall risk of
            {copy.overallRisk}
          </p>
        )}
        <button
          type="button"
          className="concern-toggle"
          aria-expanded={showModel}
          onClick={() => setShowModel(!showModel)}
        >
          <ChevronDown size={16} className={showModel ? "rotated" : ""} />
          {copy.howModel}
        </button>
        {showModel && (
          <div className="model-detail">
            <p>{risk.model_basis}</p>
            <div className="model-stats">
              {risk.model_n ? <div><span>{copy.trainingRecords}</span><strong>{risk.model_n.toLocaleString()}</strong></div> : null}
              {risk.model_years.length ? <div><span>{copy.years}</span><strong>{risk.model_years.join(", ")}</strong></div> : null}
              {risk.model_auc ? <div><span>{copy.heldOutAuc}</span><strong>{risk.model_auc.toFixed(3)}</strong></div> : null}
              {risk.model_holdout_years.length ? <div><span>{copy.testedOn}</span><strong>{risk.model_holdout_years.join(", ")}</strong></div> : null}
            </div>
            {risk.model_tokens.length > 0 && (
              <>
                <h5>{copy.keyedOn.replace("{possessive}", audience === "patient" ? "your" : "their")}</h5>
                <ul className="token-list">
                  {risk.model_tokens.map((token) => <li key={token}>{token}</li>)}
                </ul>
                <p className="driver-caveat">
                  {copy.modelCaveat}
                </p>
              </>
            )}
          </div>
        )}
        <p className="risk-ladder-note">{copy.ladderNote}</p>
      </div>
    </section>
  );
}
