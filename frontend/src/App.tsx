import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowRight,
  Download,
  Globe2,
  HeartPulse,
  LogOut,
  Mic,
  Pill,
  UserRound,
  X,
} from "lucide-react";
import { demoSenior } from "./mock";
import { api } from "./api";
import { supabase, supabaseConfigured } from "./supabase";
import type { CarePlan, CheckIn, CheckInResponse, HandoffPacket, Senior } from "./types";
import { languageOptions, supportedLanguage } from "./i18n";
import Caregiver, { caregiverRoute } from "./Caregiver";

type Credentials = { email: string; password: string };
type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string }; isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
};
type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;
function speakText(text: string, language: string) {
  if (!("speechSynthesis" in window)) return false;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = language === "zh" ? "zh-CN" : language === "pt" ? "pt-BR" : language === "hi" ? "hi-IN" : language === "es" ? "es-ES" : language === "fr" ? "fr-FR" : "en-US";
  utterance.rate = 0.9;
  window.speechSynthesis.speak(utterance);
  return true;
}
const splitList = (value: FormDataEntryValue | null) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const presetCheckinText: Record<string, Record<string, string>> = {
  "I felt a little dizzy when I stood up this morning.": {
    es: "Me sentí un poco mareada al levantarme esta mañana.",
    pt: "Senti um pouco de tontura quando me levantei esta manhã.",
    zh: "今天早上起床时，我感到有点头晕。",
    hi: "आज सुबह खड़े होने पर मुझे थोड़ा चक्कर आया।",
  },
  "My knees hurt after the walk, but I am resting now.": {
    es: "Me duelen las rodillas después de caminar, pero ahora estoy descansando.",
    pt: "Meus joelhos doeram depois da caminhada, mas agora estou descansando.",
    zh: "散步后我的膝盖疼，不过现在正在休息。",
    hi: "टहलने के बाद मेरे घुटनों में दर्द हुआ, लेकिन अब मैं आराम कर रहा/रही हूँ।",
  },
  "No new concerns today.": {
    es: "No tengo nuevas preocupaciones hoy.",
    pt: "Não tenho novas preocupações hoje.",
    zh: "今天没有新的不适。",
    hi: "आज कोई नई चिंता नहीं है।",
  },
};

function displayCheckinText(text: string | undefined, language: string) {
  return presetCheckinText[text || ""]?.[language] || text;
}

type FollowUpKey = "back-severity" | "back-warning-signs";
type ConversationTurn = { speaker: "senior" | "carepath"; text: string };
const followUpPrompts: Record<FollowUpKey, Record<string, string>> = {
  "back-severity": {
    en: "How bad is the back pain from 0 to 10, and did it start suddenly?",
    es: "\u00bfQu\u00e9 tan fuerte es el dolor de espalda del 0 al 10 y comenz\u00f3 de repente?",
    pt: "De 0 a 10, qu\u00e3o forte \u00e9 a dor nas costas? Come\u00e7ou de repente?",
    zh: "\u8170\u80cc\u75bc\u75db\u4ece0到10有多\u75db？是突然开始的吗？",
    hi: "कमर दर्द 0 से 10 में कितना तेज है? क्या यह अचानक शुरू हुआ?",
  },
  "back-warning-signs": {
    en: "Do you have leg weakness or numbness, trouble walking, or trouble controlling your bladder or bowels?",
    es: "\u00bfTiene debilidad o adormecimiento en las piernas, dificultad para caminar o para controlar la vejiga o el intestino?",
    pt: "Voc\u00ea tem fraqueza ou dorm\u00eancia nas pernas, dificuldade para andar ou para controlar a bexiga ou o intestino?",
    zh: "您的腿是否无力或麻木、走路困难，或无法控制大小便？",
    hi: "क्या आपके पैरों में कमजोरी या सुन्नपन, चलने में परेशानी, या पेशाब या मल पर नियंत्रण में परेशानी है?",
  },
};
function followUpText(key: FollowUpKey, language: string) {
  return followUpPrompts[key][language] || followUpPrompts[key].en;
}

function pdfEscape(value: string) {
  return value.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}
function makeHandoffPdf(lines: string[]) {
  const chunks: string[] = [];
  let y = 780;
  for (const line of lines.join("\n").split("\n")) {
    const safe = pdfEscape(line.slice(0, 115));
    chunks.push(`BT /F1 10 Tf 48 ${y} Td (${safe}) Tj ET`);
    y -= 14;
    if (y < 48) break;
  }
  const stream = chunks.join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 828] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (let index = 1; index < offsets.length; index += 1) pdf += `${String(offsets[index]).padStart(10, "0")} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return new Blob([pdf], { type: "application/pdf" });
}

const emptyCarePlan: CarePlan = { routines: [], instructions: [], appointments: [] };
function savedValue<T>(key: string): T | null {
  try {
    const value = localStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : null;
  } catch {
    return null;
  }
}
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <HeartPulse size={24} />
      </span>
      carepath
    </div>
  );
}
function Field({
  id,
  label,
  type = "text",
  required = false,
}: {
  id: string;
  label: string;
  type?: string;
  required?: boolean;
}) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <input id={id} name={id} type={type} required={required} />
    </div>
  );
}
function LanguagePicker() {
  const { t, i18n } = useTranslation();
  return (
    <label className="language-picker">
      <Globe2 size={18} />
      {t("language")}
      <select
        value={i18n.language}
        onChange={(event) => i18n.changeLanguage(event.target.value)}
      >
        {languageOptions.map(([code, name]) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}

function Login({
  onSignIn,
  onCreate,
}: {
  onSignIn: (credentials: Credentials) => Promise<void>;
  onCreate: () => void;
}) {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await onSignIn({ email, password });
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "";
      setError(
        detail.toLowerCase().includes("email not confirmed")
          ? t("emailNotConfirmed")
          : detail
            ? `${t("signInFailed")} ${detail}`
            : t("signInFailed"),
      );
    }
  }
  return (
    <main className="login-page">
      <section className="login-copy">
        <Brand />
        <div className="login-message">
          <h1>Health support, made simple.</h1>
        </div>
      </section>
      <section className="login-panel">
        <form className="sign-in-box" onSubmit={submit}>
          <LanguagePicker />
          <h2>{t("login")}</h2>
          <div>
            <label htmlFor="email">{t("email")}</label>
            <input
              id="email"
              name="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
          <div>
            <label htmlFor="password">{t("password")}</label>
            <input
              id="password"
              name="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button className="primary-button big-button">
            {t("login")} <ArrowRight size={20} />
          </button>
          <button
            type="button"
            className="secondary-button big-button create-profile-button"
            onClick={onCreate}
          >
            {t("create")}
          </button>
        </form>
      </section>
    </main>
  );
}

function Signup({
  onBack,
  onSave,
  existingUserId,
}: {
  onBack: () => void;
  onSave: (senior: Senior, plan: CarePlan, credentials: Credentials) => Promise<"signed-in" | "confirm-email" | "profile-pending">;
  existingUserId?: string;
}) {
  const { t, i18n } = useTranslation();
  const [medicationCount, setMedicationCount] = useState(1);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);
  const [step, setStep] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const steps = [t("account"), t("about"), t("healthInfo"), t("plan"), t("emergency")];

  function moveTo(nextStep: number) {
    setError("");
    setStep(Math.max(0, Math.min(nextStep, steps.length - 1)));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function validateStep(stepToValidate: number, form: FormData) {
    if (stepToValidate === 0 && !existingUserId) {
      const email = String(form.get("account-email") || "");
      const password = String(form.get("account-password") || "");
      if (!email || password.length < 8) {
        setError(t("accountRequired"));
        return false;
      }
      if (password !== String(form.get("confirm-password") || "")) {
        setError(t("passwordMismatch"));
        return false;
      }
    }
    if (stepToValidate === 1 && (!form.get("name") || !form.get("birthday") || !form.get("phone"))) {
      setError(t("aboutRequired"));
      return false;
    }
    if (stepToValidate === 4 && (!form.get("contact-name") || !form.get("relationship") || !form.get("contact-phone"))) {
      setError(t("contactRequired"));
      return false;
    }
    return true;
  }

  function continueToNext() {
    if (formRef.current && validateStep(step, new FormData(formRef.current))) moveTo(step + 1);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    for (let index = 0; index < steps.length; index += 1) {
      if (!validateStep(index, form)) {
        setStep(index);
        return;
      }
    }
    const email = String(form.get("account-email"));
    const password = String(form.get("account-password"));
    const birthday = String(form.get("birthday"));
    const medications = Array.from({ length: medicationCount }, (_, index) => ({
      name: String(form.get(`medicine-${index}`) || ""),
      dose: String(form.get(`dose-${index}`) || ""),
      schedule: String(form.get(`schedule-${index}`) || ""),
    }))
      .filter((item) => item.name)
      .map((item, index) => ({ ...item, id: String(index) }));
    const senior: Senior = {
      id: "new-senior",
      display_name: String(form.get("name")),
      date_of_birth: birthday,
      age:
        new Date().getFullYear() -
        new Date(`${birthday}T12:00:00`).getFullYear(),
      gender: String(form.get("gender") || ""),
      preferred_language: i18n.language,
      phone_e164: String(form.get("phone")),
      conditions: splitList(form.get("conditions")),
      allergies: splitList(form.get("allergies")),
      medications,
      consent: { sms_reminders: form.get("sms") === "on" },
      caregivers: [
        {
          id: "contact",
          name: String(form.get("contact-name")),
          relationship: String(form.get("relationship")),
          phone_e164: String(form.get("contact-phone")),
        },
      ],
    };
    const plan: CarePlan = {
      routines: splitList(form.get("routine")),
      instructions: splitList(form.get("instruction")),
      appointments: form.get("appointment")
        ? [
            {
              id: "a1",
              title: String(form.get("appointment")),
              date: String(form.get("date") || ""),
              location: String(form.get("location") || ""),
            },
          ]
        : [],
    };
    setSaving(true);
    setError("");
    try {
      const result = await onSave(senior, plan, { email, password });
      if (result === "confirm-email") setSuccess(t("confirmationSent"));
      if (result === "profile-pending") setSuccess(t("accountCreatedProfilePending"));
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "";
      if (detail.toLowerCase().includes("email rate limit")) {
        setError(t("emailRateLimited"));
      } else {
        setError(detail ? `${t("signUpFailed")} ${detail}` : t("signUpFailed"));
      }
    } finally {
      setSaving(false);
    }
  }
  return (
    <main className="onboarding-page">
      <header className="onboarding-header">
        <Brand />
        <button className="back-button" onClick={onBack}>
          {t("back")}
        </button>
      </header>
      <section className="onboarding-content">
        <LanguagePicker />
        <h1>{t("setup")}</h1>
        <form className="profile-form" onSubmit={submit} ref={formRef}>
          <p className="signup-progress" aria-live="polite">
            {t("step", { current: step + 1, total: steps.length })}: <strong>{steps[step]}</strong>
          </p>
          <section hidden={step !== 0} aria-labelledby="signup-account">
            <h2 id="signup-account">{t("account")}</h2>
            <p>{t("oneThing")}</p>
            {existingUserId ? <p>{t("alreadySignedIn")}</p> : (
              <div className="form-grid">
                <div className="full"><Field id="account-email" label={t("email")} type="email" /></div>
                <Field id="account-password" label={t("password")} type="password" />
                <Field id="confirm-password" label={t("confirmPassword")} type="password" />
              </div>
            )}
          </section>
          <section hidden={step !== 1} aria-labelledby="signup-about">
            <h2 id="signup-about">{t("about")}</h2>
            <p>{t("oneThing")}</p>
            <div className="form-grid">
              <div className="full">
                <Field id="name" label={t("fullName")} />
              </div>
              <Field id="birthday" label={t("birthday")} type="date" />
              <div>
                <label htmlFor="gender">{t("gender")}</label>
                <select className="form-select" id="gender" name="gender">
                  <option value="">{t("genderOptional")}</option>
                  <option>{t("woman")}</option>
                  <option>{t("man")}</option>
                  <option>{t("nonBinary")}</option>
                  <option>{t("selfDescribe")}</option>
                </select>
              </div>
              <div className="full">
                <Field id="phone" label={t("phone")} type="tel" />
                <label className="sms-consent">
                  <input name="sms" type="checkbox" />
                  {t("reminders")}
                </label>
              </div>
            </div>
          </section>
          <section hidden={step !== 2} aria-labelledby="signup-health">
            <h2 id="signup-health">{t("healthInfo")}</h2>
            <p>{t("optionalStep")}</p>
            <div className="form-grid">
              <div className="full">
                <Field id="conditions" label={t("conditions")} />
              </div>
              <div className="full">
                <Field id="allergies" label={t("allergies")} />
              </div>
            </div>
            <div className="medication-heading">
              <h3>{t("medications")}</h3>
              <button
                type="button"
                className="add-medication"
                onClick={() => setMedicationCount((count) => count + 1)}
              >
                {t("add")}
              </button>
            </div>
            {Array.from({ length: medicationCount }, (_, index) => (
              <fieldset className="medication-entry" key={index}>
                <legend>
                  {t("medications")} {index + 1}
                </legend>
                <div className="form-grid">
                  <Field id={`medicine-${index}`} label={t("medicineName")} />
                  <Field id={`dose-${index}`} label={t("dose")} />
                  <div className="full">
                    <Field id={`schedule-${index}`} label={t("schedule")} />
                  </div>
                </div>
              </fieldset>
            ))}
          </section>
          <section hidden={step !== 3} aria-labelledby="signup-plan">
            <h2 id="signup-plan">{t("plan")}</h2>
            <p>{t("optionalStep")}</p>
            <div className="form-grid">
              <div className="full">
                <Field id="routine" label={t("routine")} />
              </div>
              <div className="full">
                <Field id="instruction" label={t("instruction")} />
              </div>
              <Field id="appointment" label={t("appointment")} />
              <Field id="date" label={t("dateTime")} />
              <div className="full">
                <Field id="location" label={t("location")} />
              </div>
            </div>
          </section>
          <section hidden={step !== 4} aria-labelledby="signup-emergency">
            <h2 id="signup-emergency">{t("emergency")}</h2>
            <p>{t("oneThing")}</p>
            <div className="form-grid">
              <Field id="contact-name" label={t("fullName")} />
              <Field id="relationship" label={t("relationship")} />
              <div className="full">
                <Field
                  id="contact-phone"
                  label={t("phone")}
                  type="tel"
                />
              </div>
            </div>
          </section>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {success && <p className="form-success" role="status">{success}</p>}
          <div className="signup-actions">
            <button
              type="button"
              className="secondary-button big-button"
              onClick={() => (success || step === 0 ? onBack() : moveTo(step - 1))}
            >
              {success ? t("login") : t("back")}
            </button>
            {success ? null : step < steps.length - 1 ? (
              <button type="button" className="primary-button big-button" onClick={continueToNext}>
                {t("continue")} <ArrowRight size={20} />
              </button>
            ) : (
              <button className="primary-button big-button" disabled={saving}>
                {saving ? t("creatingAccount") : t("save")} <ArrowRight size={20} />
              </button>
            )}
          </div>
        </form>
      </section>
    </main>
  );
}

function Profile({
  senior,
  plan,
  onClose,
}: {
  senior: Senior;
  plan: CarePlan;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const contact = senior.caregivers?.[0];
  const Section = ({ title, values }: { title: string; values: string[] }) => (
    <section className="profile-section">
      <h3>{title}</h3>
      {values.length ? (
        <ul className="profile-list">
          {values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">{t("none")}</p>
      )}
    </section>
  );
  return (
    <div className="modal-backdrop">
      <section
        className="modal profile-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t("profile")}
      >
        <button
          className="icon-button close"
          onClick={onClose}
          aria-label="Close"
        >
          <X />
        </button>
        <div className="profile-header">
          <h2>{senior.display_name}</h2>
          <p>
            {senior.age} · {senior.gender || t("genderOptional")}
            <br />
            {senior.phone_e164}
          </p>
        </div>
        <div className="profile-grid">
          <Section title={t("conditions")} values={senior.conditions} />
          <Section title={t("allergies")} values={senior.allergies} />
          <Section title={t("dailyRoutine")} values={plan.routines} />
          <Section title={t("instructions")} values={plan.instructions} />
          <section className="profile-section">
            <h3>{t("medications")}</h3>
            {senior.medications.length ? (
              senior.medications.map((medication) => (
                <p className="compact-item" key={medication.id}>
                  <Pill size={18} /> <strong>{medication.name}</strong> ·{" "}
                  {medication.dose}
                  <br />
                  {medication.schedule}
                </p>
              ))
            ) : (
              <p className="muted">{t("none")}</p>
            )}
          </section>
          <Section
            title={t("appointment")}
            values={plan.appointments.map(
              (item) => `${item.title} · ${item.date}`,
            )}
          />
          {contact && (
            <Section
              title={t("emergency")}
              values={[
                `${contact.name} · ${contact.relationship} · ${contact.phone_e164}`,
              ]}
            />
          )}
        </div>
      </section>
    </div>
  );
}

function Dashboard({
  senior,
  plan,
  onSignOut,
}: {
  senior: Senior;
  plan: CarePlan;
  onSignOut: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [page, setPage] = useState<"health" | "plan" | "history">("health");
  const [profileOpen, setProfileOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [checkins, setCheckins] = useState<CheckIn[]>([]);
  const [evaluation, setEvaluation] = useState<CheckInResponse["evaluation"] | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [listening, setListening] = useState(false);
  const [followUpKey, setFollowUpKey] = useState<FollowUpKey | null>(null);
  const [conversationTurns, setConversationTurns] = useState<ConversationTurn[]>([]);
  const [downloadingHandoff, setDownloadingHandoff] = useState(false);
  const [reminderKind, setReminderKind] = useState("meds");
  const [sendingReminder, setSendingReminder] = useState(false);
  const [reminderStatus, setReminderStatus] = useState("");
  const [expandedCheckin, setExpandedCheckin] = useState<string | null>(null);
  const [checkinDetails, setCheckinDetails] = useState<Record<string, CheckInResponse>>({});
  const [handoffDetails, setHandoffDetails] = useState<Record<string, HandoffPacket>>({});
  const [speaking, setSpeaking] = useState(false);
  const languageLoaded = useRef(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const messageRef = useRef("");
  const profileRegistered = useRef(false);
  const guidanceTone = evaluation && (evaluation.level >= 4 ? "emergency" : evaluation.level >= 3 ? "urgent" : evaluation.level === 2 ? "watch" : "calm");
  useEffect(() => {
    const voiceLanguage = (event: Event) =>
      i18n.changeLanguage(
        supportedLanguage(
          (event as CustomEvent<{ language?: string }>).detail?.language,
        ),
      );
    window.addEventListener("carepath:voice-language", voiceLanguage);
    void (async () => {
      try {
        // The MVP FastAPI store is in-memory, so a server restart loses the
        // profile. Re-sending this idempotent profile restores that session.
        await api.createSenior(senior);
        profileRegistered.current = true;
        setCheckins(await api.checkins(senior.id));
      } catch {
        // The dashboard remains available while the local API is offline.
      }
    })();
    return () =>
      window.removeEventListener("carepath:voice-language", voiceLanguage);
  }, [i18n, senior.id]);
  useEffect(() => {
    const saved = localStorage.getItem(`carepath-language:${senior.id}`);
    if (saved) i18n.changeLanguage(supportedLanguage(saved));
    languageLoaded.current = true;
  }, [i18n, senior.id]);
  useEffect(() => {
    if (languageLoaded.current) localStorage.setItem(`carepath-language:${senior.id}`, supportedLanguage(i18n.language));
  }, [i18n.language, senior.id]);
  useEffect(() => {
    const latest = checkins[0];
    if (!latest || !evaluation) return;
    void api.checkin(latest.id, i18n.language)
      .then((result) => setEvaluation(result.evaluation))
      .catch(() => {
        // Keep the last safely generated guidance if the API is offline.
      });
  }, [checkins, i18n.language]);
  const updateMessage = (nextMessage: string) => {
    messageRef.current = nextMessage;
    setMessage(nextMessage);
  };
  const addCheckin = async (text = messageRef.current, source = "text") => {
    if (!text.trim() || saving) return;
    setSaving(true);
    setError("");
    const askedKey = followUpKey;
    try {
      if (!profileRegistered.current) {
        await api.createSenior(senior);
        profileRegistered.current = true;
      }
      const result = await api.createCheckIn({
        senior_id: senior.id,
        text: text.trim(),
        language: i18n.language,
        source,
      });
      setCheckins((current) => [result.checkin, ...current]);
      setEvaluation(result.evaluation);
      setConversationTurns((current) => [
        ...current,
        ...(askedKey ? [{ speaker: "carepath" as const, text: followUpText(askedKey, i18n.language) }] : []),
        { speaker: "senior" as const, text: text.trim() },
      ]);
      if (followUpKey === "back-severity") {
        setFollowUpKey("back-warning-signs");
      } else if (followUpKey === "back-warning-signs") {
        setFollowUpKey(null);
      } else if (result.checkin.symptoms.some((symptom) => symptom.label === "back pain")) {
        setFollowUpKey("back-severity");
      } else {
        setFollowUpKey(null);
      }
      updateMessage("");
    } catch (reason) {
      const detail = reason instanceof Error ? ` ${reason.message}` : "";
      setError(`This check-in could not be saved. Please start the health service and try again.${detail}`);
    } finally {
      setSaving(false);
    }
  };
  const downloadHandoff = async () => {
    if (!evaluation || evaluation.level < 3 || downloadingHandoff) return;
    setDownloadingHandoff(true);
    setError("");
    try {
      const packet = await api.handoff(senior.id);
      const flags = packet.red_flags.map((flag) => `- ${flag.label}`).join("\n") || "- None recorded";
      const medications = packet.medications.map((medication) => `- ${medication.name}${medication.dose ? ` (${medication.dose})` : ""}`).join("\n") || "- None recorded";
      const blob = makeHandoffPdf([
        "CarePath nurse handoff",
        `Patient: ${senior.display_name} (${senior.age})`,
        `Created: ${packet.created_at}`,
        `Action level: ${packet.level}`,
        `Presenting complaint: ${packet.presenting_complaint}`,
        "",
        "Summary:", packet.patient_summary_en,
        "", "Red flags:", flags,
        "", "Medications:", medications,
        `Allergies: ${packet.allergies.join(", ") || "None recorded"}`,
        "", packet.disclaimer,
      ]);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `carepath-handoff-${senior.id}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(`The nurse handoff could not be downloaded${reason instanceof Error ? `: ${reason.message}` : "."}`);
    } finally {
      setDownloadingHandoff(false);
    }
  };
  const sendReminder = async () => {
    setSendingReminder(true);
    setReminderStatus("");
    try {
      const result = await api.sendReminder(senior.id, reminderKind, i18n.language);
      const recipients = result.recipients?.length ? ` to ${result.recipients.length} phones (senior + caregiver)` : ` to ${result.recipient || "the care circle"}`;
      setReminderStatus(result.ok ? `${result.mocked ? "Demo message queued" : "Message sent"}${recipients}. Reply DONE is expected.` : (result.error || "Message was not sent."));
    } catch (reason) {
      setReminderStatus(reason instanceof Error ? reason.message : "Message was not sent.");
    } finally {
      setSendingReminder(false);
    }
  };
  const openCheckin = async (checkin: CheckIn) => {
    if (expandedCheckin === checkin.id) {
      setExpandedCheckin(null);
      return;
    }
    setExpandedCheckin(checkin.id);
    if (checkinDetails[checkin.id]) return;
    try {
      const detail = await api.checkin(checkin.id, i18n.language);
      setCheckinDetails((current) => ({ ...current, [checkin.id]: detail }));
      if (detail.evaluation.level >= 3) {
        const packet = await api.handoff(senior.id);
        setHandoffDetails((current) => ({ ...current, [checkin.id]: packet }));
      }
    } catch {
      // Demo history has no backend IDs; the current guidance remains useful.
      if (evaluation) setCheckinDetails((current) => ({ ...current, [checkin.id]: { checkin, evaluation } }));
    }
  };
  const toggleListening = () => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      return;
    }
    const speechWindow = window as unknown as {
      SpeechRecognition?: SpeechRecognitionConstructor;
      webkitSpeechRecognition?: SpeechRecognitionConstructor;
    };
    const Recognition = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
    if (!Recognition) {
      setError(t("voiceNotSupported"));
      return;
    }
    const recognition = new Recognition();
    const startingText = message.trim();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = i18n.language === "zh" ? "zh-CN" : i18n.language === "pt" ? "pt-BR" : i18n.language === "hi" ? "hi-IN" : i18n.language === "es" ? "es-ES" : "en-US";
    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (result.isFinal) finalText += result[0].transcript;
        else interimText += result[0].transcript;
      }
      updateMessage([startingText, finalText, interimText].filter(Boolean).join(" ").trim());
    };
    recognition.onerror = () => {
      setError(t("voiceNotSupported"));
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
    };
    recognitionRef.current = recognition;
    setError("");
    setListening(true);
    recognition.start();
  };
  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand />
        <nav>
          <button
            className={page === "health" ? "nav-active" : ""}
            onClick={() => setPage("health")}
          >
            {t("health")}
          </button>
          <button
            className={page === "plan" ? "nav-active" : ""}
            onClick={() => setPage("plan")}
          >
            {t("plan")}
          </button>
          <button
            className={page === "history" ? "nav-active" : ""}
            onClick={() => setPage("history")}
          >
            {t("history")}
          </button>
        </nav>
        <div className="topbar-actions">
          <LanguagePicker />
          <button
            className="profile-button"
            onClick={() => setProfileOpen(true)}
          >
            <UserRound size={18} />
            {t("profile")}
          </button>
          <button className="text-button" onClick={onSignOut}>
            <LogOut size={17} />
            {t("signOut")}
          </button>
        </div>
      </header>
      {page === "health" && (
        <main className="dashboard">
          <div className="greeting">
            <h1>
              {t("greeting", { name: senior.display_name.split(" ")[0] })}
            </h1>
            <p>{t("feeling")}</p>
          </div>
          <section className="checkin-card">
            {followUpKey && (
              <div className="followup-question" role="status">
                <p className="eyebrow">One more question</p>
                <h3>{followUpText(followUpKey, i18n.language)}</h3>
                <p className="helper-text">Your answer helps us choose the safest next step.</p>
              </div>
            )}
            <h2>{t("tell")}</h2>
            <textarea
              value={message}
              onChange={(event) => updateMessage(event.target.value)}
              rows={4}
            />
            <div className="checkin-actions">
              <button className="voice-button" type="button" onClick={toggleListening} aria-pressed={listening}>
                <Mic size={20} />
                {listening ? t("stopListening") : t("speak")}
              </button>
              <button
                className="primary-button big-button"
                disabled={!message.trim() || saving}
                onClick={() => void addCheckin()}
              >
                {saving ? "Saving…" : t("check")} <ArrowRight size={20} />
              </button>
            </div>
            {listening && <p className="live-transcript" role="status">{t("listening")}</p>}
            {error && <p className="form-error" role="alert">{error}</p>}
          </section>
          {conversationTurns.length > 0 && (
            <section className="conversation-card" aria-label="Conversation">
              <div className="section-heading"><h2>Conversation</h2><span className="language-status">Safety questions are checked after every answer</span></div>
              <div className="conversation-turns">
                {conversationTurns.map((turn, index) => <p className={turn.speaker === "carepath" ? "carepath-turn" : "senior-turn"} key={`${index}-${turn.text}`}><strong>{turn.speaker === "carepath" ? "CarePath" : senior.display_name.split(" ")[0]}:</strong> {turn.text}</p>)}
              </div>
            </section>
          )}
          <section className="reminder-card">
            <div>
              <p className="eyebrow">Linq family messaging</p>
              <h2>Text + read-aloud reminders</h2>
              <p className="helper-text">Medication, appointment, refill, weather, and caregiver updates are texted to the senior and the first consented caregiver. The local demo uses Docker mock delivery.</p>
            </div>
            <div className="reminder-actions">
              <select value={reminderKind} onChange={(event) => setReminderKind(event.target.value)} aria-label="Reminder type">
                <option value="meds">Morning pills</option>
                <option value="appointment">Appointment</option>
                <option value="refill">Refill</option>
                <option value="weather">Weather</option>
                <option value="caregiver_update">Caregiver update</option>
              </select>
              <button className="secondary-button" type="button" onClick={() => void sendReminder()} disabled={sendingReminder}>
                {sendingReminder ? "Sending..." : "Send to phone"}
              </button>
            </div>
            {reminderStatus && <p className="form-success" role="status">{reminderStatus}</p>}
            <button className="text-button reminder-preview" type="button" onClick={() => {
              const text = reminderKind === "meds" ? "Good morning. Just a gentle reminder to take your morning pills. Reply DONE when you have taken them." : reminderKind === "appointment" ? "Hi there. Just a friendly reminder about your appointment. Reply DONE when you have seen it." : reminderKind === "refill" ? "Hi there. Your refill reminder is ready. Reply DONE when you have seen it." : reminderKind === "caregiver_update" ? "Hi there. Your care team has a quick update for you. Reply DONE when you have seen it." : "Good morning. I have a quick weather reminder for you. Reply DONE when you have seen it.";
              if (!speakText(text, i18n.language)) setReminderStatus("Read-aloud is not available in this browser.");
            }}>🔊 Read this reminder aloud</button>
          </section>
          {evaluation && <section className={`guidance-card ${guidanceTone}`} aria-live="polite">
            <div className="guidance-content">
              <p className="guidance-level">{evaluation.level_label}</p>
              <h2>{t("guidance")}</h2>
              <p>{evaluation.explanation}</p>
              {evaluation.recommended_actions.length > 0 && (
                <ul className="guidance-list">
                  {evaluation.recommended_actions.map((action) => <li key={action}>{action}</li>)}
                </ul>
              )}
              <button className="text-button" type="button" onClick={() => {
                setSpeaking(true);
                if (!speakText(evaluation.explanation, i18n.language)) setError("Read-aloud is not available in this browser.");
                window.setTimeout(() => setSpeaking(false), Math.max(1200, evaluation.explanation.length * 45));
              }}>{speaking ? "Speaking…" : "🔊 Read guidance aloud"}</button>
              {evaluation.level >= 3 && <button className="secondary-button" type="button" onClick={() => void downloadHandoff()} disabled={downloadingHandoff}><Download size={18} /> {downloadingHandoff ? "Preparing handoff..." : "Download nurse handoff PDF"}</button>}
            </div>
          </section>}
        </main>
      )}
      {page === "plan" && (
        <main className="dashboard">
          <h1>{t("plan")}</h1>
          <PlanSection title={t("dailyRoutine")} values={plan.routines} />
          <PlanSection title={t("instructions")} values={plan.instructions} />
          <PlanSection
            title={t("appointment")}
            values={plan.appointments.map(
              (item) => `${item.title} · ${item.date}`,
            )}
          />
        </main>
      )}
      {page === "history" && (
        <main className="dashboard">
          <h1>{t("history")}</h1>
          {checkins.map((checkin) => (
            <article className="history-record" key={checkin.id}>
              <button className="history-record-button" type="button" onClick={() => void openCheckin(checkin)} aria-expanded={expandedCheckin === checkin.id}>
                <strong>{checkin.symptoms.map((symptom) => t(`symptom.${symptom.label}`, { defaultValue: symptom.label })).join(", ") || t("check")}</strong>
                <p>{checkin.created_at}</p>
                <p>{displayCheckinText(checkin.raw_text, i18n.language)}</p>
              </button>
              {expandedCheckin === checkin.id && checkinDetails[checkin.id] && (
                <div className="history-detail">
                  <h2>{t("suggestedAction")}</h2>
                  <p className="history-action-level">{checkinDetails[checkin.id].evaluation.level_label}</p>
                  <p>{checkinDetails[checkin.id].evaluation.explanation}</p>
                  <ul>{checkinDetails[checkin.id].evaluation.recommended_actions.map((action) => <li key={action}>{action}</li>)}</ul>
                  {checkinDetails[checkin.id].evaluation.level >= 3 && handoffDetails[checkin.id] && (
                    <div className="handoff-summary">
                      <h3>AI provider handoff summary</h3>
                      <p>{handoffDetails[checkin.id].patient_summary_en}</p>
                      <p className="muted">This summary is generated from the reported check-in and is for clinician review.</p>
                    </div>
                  )}
                </div>
              )}
            </article>
          ))}
        </main>
      )}
      {profileOpen && (
        <Profile
          senior={senior}
          plan={plan}
          onClose={() => setProfileOpen(false)}
        />
      )}
    </div>
  );
}
function PlanSection({ title, values }: { title: string; values: string[] }) {
  const { t } = useTranslation();
  return (
    <section className="care-plan-card">
      <h2>{title}</h2>
      {values.length ? (
        <ul>
          {values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">{t("none")}</p>
      )}
    </section>
  );
}

export default function App() {
  // The link in every Linq text lands on /c/<senior_id>. It is a public,
  // read-mostly view for a caregiver who is not signed in, so it is checked
  // before any of the account flow below.
  const caregiver = caregiverRoute(window.location.pathname);
  if (caregiver) return <Caregiver token={caregiver.token} />;
  return <AccountApp />;
}

function AccountApp() {
  const { i18n } = useTranslation();
  const [screen, setScreen] = useState<"login" | "signup" | "dashboard">(
    "login",
  );
  const [senior, setSenior] = useState<Senior>(demoSenior);
  const [plan, setPlan] = useState<CarePlan>(emptyCarePlan);
  const [profileSetupUserId, setProfileSetupUserId] = useState<string | null>(null);
  const signIn = async (input: Credentials) => {
    if (!supabase) throw new Error("Supabase is not configured");
    const { data, error } = await supabase.auth.signInWithPassword(input);
    if (error) throw error;
    if (!data.user) throw new Error("Signed in, but no user profile was returned");
    const profileKey = `carepath-profile:${data.user.id}`;
    const planKey = `carepath-plan:${data.user.id}`;
    let profile = savedValue<Senior>(profileKey);
    if (!profile) {
      try {
        profile = await api.senior(data.user.id);
        localStorage.setItem(profileKey, JSON.stringify(profile));
      } catch {
        return { profile: null, plan: emptyCarePlan, userId: data.user.id };
      }
    }
    return { profile, plan: savedValue<CarePlan>(planKey) || emptyCarePlan, userId: data.user.id };
  };
  const createAccount = async (person: Senior, carePlan: CarePlan, account: Credentials, existingUserId?: string) => {
    if (!supabaseConfigured || !supabase) throw new Error("Supabase is not configured");
    let userId = existingUserId;
    let hasSession = Boolean(existingUserId);
    if (!userId) {
      const { data, error } = await supabase.auth.signUp({
        email: account.email,
        password: account.password,
        options: {
          data: {
            display_name: person.display_name,
            preferred_language: person.preferred_language,
            phone: person.phone_e164,
          },
        },
      });
      if (error) throw error;
      if (!data.user) throw new Error("Supabase did not return a user");
      userId = data.user.id;
      hasSession = Boolean(data.session);
    }
    const savedPerson = { ...person, id: userId };
    localStorage.setItem(`carepath-profile:${savedPerson.id}`, JSON.stringify(savedPerson));
    localStorage.setItem(`carepath-plan:${savedPerson.id}`, JSON.stringify(carePlan));
    try {
      await api.createSenior(savedPerson);
    } catch {
      // The auth account exists even if the local FastAPI service is offline.
      // Do not present that as a failed account creation.
      return "profile-pending" as const;
    }
    setSenior(savedPerson);
    setPlan(carePlan);
    i18n.changeLanguage(supportedLanguage(savedPerson.preferred_language));
    if (hasSession) {
      setScreen("dashboard");
      return "signed-in" as const;
    }
    return "confirm-email" as const;
  };
  if (screen === "signup")
    return (
      <Signup
        onBack={() => setScreen("login")}
        existingUserId={profileSetupUserId || undefined}
        onSave={(person, carePlan, account) => createAccount(person, carePlan, account, profileSetupUserId || undefined)}
      />
    );
  return screen === "dashboard" ? (
    <Dashboard
      senior={senior}
      plan={plan}
      onSignOut={() => setScreen("login")}
    />
  ) : (
    <Login
      onSignIn={async (input) => {
        const signedIn = await signIn(input);
        if (!signedIn.profile) {
          setProfileSetupUserId(signedIn.userId);
          setScreen("signup");
          return;
        }
        setSenior(signedIn.profile);
        setPlan(signedIn.plan);
        i18n.changeLanguage(supportedLanguage(signedIn.profile.preferred_language));
        setScreen("dashboard");
      }}
      onCreate={() => setScreen("signup")}
    />
  );
}
