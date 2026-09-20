import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowRight,
  ChevronDown,
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
import type { CarePlan, CheckIn, CheckInResponse, DatasetNote, HandoffPacket, ReminderJob, RiskAssessment, RiskConcern, RiskDriver, Senior } from "./types";
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

function timeGreeting(name: string, language: string) {
  const hour = new Date().getHours();
  const part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  const greetings: Record<string, Record<string, string>> = {
    en: { morning: "Good morning", afternoon: "Good afternoon", evening: "Good evening" },
    es: { morning: "Buenos días", afternoon: "Buenas tardes", evening: "Buenas noches" },
    pt: { morning: "Bom dia", afternoon: "Boa tarde", evening: "Boa noite" },
    zh: { morning: "早上好", afternoon: "下午好", evening: "晚上好" },
    hi: { morning: "सुप्रभात", afternoon: "नमस्ते", evening: "शुभ संध्या" },
  };
  return `${(greetings[language] || greetings.en)[part]}, ${name}.`;
}

type FollowUpKey = "back-severity" | "back-warning-signs";
type FollowUpRecord = { question: string; answer: string; checkinId: string };
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
  defaultValue,
}: {
  id: string;
  label: string;
  type?: string;
  required?: boolean;
  defaultValue?: string;
}) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <input id={id} name={id} type={type} required={required} defaultValue={defaultValue} />
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
  const steps = [t("account"), t("about"), t("healthInfo"), t("plan"), t("emergencyCaregiver")];

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
            <h2 id="signup-emergency">{t("emergencyCaregiver")}</h2>
            <p>{t("caregiverDescription")}</p>
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
  onSave,
}: {
  senior: Senior;
  plan: CarePlan;
  onClose: () => void;
  onSave: (updated: Senior) => Promise<void>;
}) {
  const { t } = useTranslation();
  const contact = senior.caregivers?.[0];
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [editableMedications, setEditableMedications] = useState(senior.medications);
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
            {senior.age} · {senior.gender || t("genderOptional")} · {senior.date_of_birth}
            <br />
            {senior.phone_e164}
          </p>
          <button className="secondary-button profile-edit-button" type="button" onClick={() => setEditing((value) => !value)}>
            {editing ? t("cancel") : t("editProfile")}
          </button>
        </div>
        {editing ? (
          <form className="profile-edit-form" onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const medications = editableMedications.filter((medication) => medication.name.trim());
            const updated: Senior = {
              ...senior,
              display_name: String(form.get("name") || senior.display_name),
              phone_e164: String(form.get("phone") || senior.phone_e164),
              gender: String(form.get("gender") || ""),
              conditions: splitList(form.get("conditions")),
              allergies: splitList(form.get("allergies")),
              medications,
              caregivers: [{
                id: contact?.id || "contact",
                name: String(form.get("contact-name") || ""),
                relationship: String(form.get("relationship") || ""),
                phone_e164: String(form.get("contact-phone") || ""),
              }].filter((caregiver) => caregiver.name && caregiver.relationship && caregiver.phone_e164),
            };
            setSaving(true);
            setSaveError("");
            void onSave(updated).then(() => setEditing(false)).catch(() => setSaveError("We could not save those changes. Please try again.")).finally(() => setSaving(false));
          }}>
            <div className="form-grid">
              <Field id="name" label={t("fullName")} required defaultValue={senior.display_name} />
              <Field id="phone" label={t("phone")} type="tel" required defaultValue={senior.phone_e164} />
              <div><label htmlFor="profile-gender">{t("gender")}</label><select className="form-select" id="profile-gender" name="gender" defaultValue={senior.gender || ""}><option value="">{t("genderOptional")}</option><option>{t("woman")}</option><option>{t("man")}</option><option>{t("nonBinary")}</option><option>{t("selfDescribe")}</option></select></div>
              <div><Field id="conditions" label={t("conditions")} defaultValue={senior.conditions.join(", ")} /><p className="field-help">Separate items with commas.</p></div>
              <div className="full"><Field id="allergies" label={t("allergies")} defaultValue={senior.allergies.join(", ")} /><p className="field-help">Separate items with commas.</p></div>
              <div className="full medication-editor"><div className="medication-heading"><h3>{t("medications")}</h3><button className="add-medication" type="button" onClick={() => setEditableMedications((current) => [...current, { id: crypto.randomUUID(), name: "", dose: "", schedule: "" }])}>{t("add")}</button></div>{editableMedications.map((medication, index) => <fieldset className="medication-entry" key={medication.id}><legend>{t("medications")} {index + 1}</legend><div className="form-grid"><div><label htmlFor={`edit-med-name-${medication.id}`}>{t("medicineName")}</label><input id={`edit-med-name-${medication.id}`} value={medication.name} onChange={(event) => setEditableMedications((current) => current.map((item) => item.id === medication.id ? { ...item, name: event.target.value } : item))} /></div><div><label htmlFor={`edit-med-dose-${medication.id}`}>{t("dose")}</label><input id={`edit-med-dose-${medication.id}`} value={medication.dose || ""} onChange={(event) => setEditableMedications((current) => current.map((item) => item.id === medication.id ? { ...item, dose: event.target.value } : item))} /></div><div className="full"><label htmlFor={`edit-med-schedule-${medication.id}`}>{t("schedule")}</label><input id={`edit-med-schedule-${medication.id}`} value={medication.schedule || ""} onChange={(event) => setEditableMedications((current) => current.map((item) => item.id === medication.id ? { ...item, schedule: event.target.value } : item))} /></div></div>{editableMedications.length > 1 && <button className="remove-medication" type="button" onClick={() => setEditableMedications((current) => current.filter((item) => item.id !== medication.id))}>Remove</button>}</fieldset>)}</div>
              <div className="full"><h3>{t("emergencyCaregiver")}</h3></div>
              <Field id="contact-name" label={t("fullName")} defaultValue={contact?.name || ""} />
              <Field id="relationship" label={t("relationship")} defaultValue={contact?.relationship || ""} />
              <div className="full"><Field id="contact-phone" label={t("phone")} type="tel" defaultValue={contact?.phone_e164 || ""} /></div>
            </div>
            {saveError && <p className="form-error" role="alert">{saveError}</p>}
            <button className="primary-button" disabled={saving}>{saving ? "Saving…" : t("saveChanges")}</button>
          </form>
        ) : (
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
              title={t("emergencyCaregiver")}
              values={[
                `${contact.name} · ${contact.relationship} · ${contact.phone_e164}`,
                t("caregiverDescription"),
              ]}
            />
          )}
        </div>
        )}
      </section>
    </div>
  );
}

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

function ConcernCard({ concern, open, onToggle }: {
  concern: RiskConcern; open: boolean; onToggle: () => void;
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
        Starts at {concern.base_rate_percent.toFixed(0)}% for someone your age with this complaint
        {concern.drivers.length > 0 && <>, then moves to {concern.probability_percent.toFixed(0)}% on what you told us</>}
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

function RiskPanel({ risk }: { risk: RiskAssessment }) {
  const [openCode, setOpenCode] = useState<string | null>(risk.top_concern ?? null);
  const [showModel, setShowModel] = useState(false);
  const [showSources, setShowSources] = useState(false);
  if (!risk.concerns.length) return null;
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
            Each percentage is how often a presentation like yours ended in
            hospital rather than being sent home, adjusted for your age, your
            medicines and what you told us today.
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
                <h5>What it keyed on in your words</h5>
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

function checkinWellnessScore(checkin: CheckIn) {
  const symptoms = checkin.symptoms || [];
  const averageSeverity = symptoms.length ? symptoms.reduce((total, symptom) => total + (symptom.severity ?? 2), 0) / symptoms.length : 0;
  return Math.max(35, Math.min(100, Math.round(100 - symptoms.length * 4 - averageSeverity * 4)));
}
const wellnessCopy: Record<string, Record<string, string>> = {
  en: { overview: "Personal check-in overview", title: "Senior wellness score", disclaimer: "A check-in score, not a medical diagnosis.", latest: "Latest check-in", start: "Complete a check-in to create your score", noData: "No check-in data yet", symptoms: "Reported symptoms", safety: "Safety review", voice: "Voice input", checkins: "Check-ins", consistency: "Consistency", trend: "Wellness trend", personalScore: "Personal check-in score", insight: "Check-in insight", noTrend: "No check-ins yet. Your trend appears after the first symptom check-in.", how: "How it works", assisted: "AI-assisted check-in insights", baseline: "Personal baseline comparison only. This is not a clinical measurement or diagnosis." },
  es: { overview: "Resumen personal del registro", title: "Puntuación de bienestar", disclaimer: "Una puntuación de registro, no un diagnóstico médico.", latest: "Último registro", start: "Complete un registro para crear su puntuación", noData: "Aún no hay datos", symptoms: "Síntomas informados", safety: "Revisión de seguridad", voice: "Entrada de voz", checkins: "Registros", consistency: "Constancia", trend: "Tendencia de bienestar", personalScore: "Puntuación personal de registro", insight: "Información del registro", noTrend: "Aún no hay registros. La tendencia aparecerá después del primero.", how: "Cómo funciona", assisted: "Información de registro asistida por IA", baseline: "Comparación con su línea base personal. No es una medición ni un diagnóstico clínico." },
  pt: { overview: "Resumo pessoal do registro", title: "Pontuação de bem-estar", disclaimer: "Uma pontuação de registro, não um diagnóstico médico.", latest: "Último registro", start: "Faça um registro para criar sua pontuação", noData: "Ainda não há dados", symptoms: "Sintomas relatados", safety: "Revisão de segurança", voice: "Entrada por voz", checkins: "Registros", consistency: "Regularidade", trend: "Tendência de bem-estar", personalScore: "Pontuação pessoal do registro", insight: "Informação do registro", noTrend: "Ainda não há registros. A tendência aparecerá após o primeiro.", how: "Como funciona", assisted: "Informações de registro assistidas por IA", baseline: "Comparação com sua linha de base pessoal. Não é uma medição ou diagnóstico clínico." },
  zh: { overview: "个人记录概览", title: "健康评分", disclaimer: "这是记录评分，不是医学诊断。", latest: "最新记录", start: "完成一次记录以生成评分", noData: "暂无记录数据", symptoms: "报告的症状", safety: "安全评估", voice: "语音输入", checkins: "记录次数", consistency: "规律性", trend: "健康趋势", personalScore: "个人记录评分", insight: "记录提示", noTrend: "暂无记录。首次症状记录后将显示趋势。", how: "工作方式", assisted: "AI 辅助记录提示", baseline: "仅与个人基线比较。这不是临床测量或诊断。" },
  hi: { overview: "व्यक्तिगत चेक-इन सारांश", title: "स्वास्थ्य स्कोर", disclaimer: "यह चेक-इन स्कोर है, चिकित्सा निदान नहीं।", latest: "नवीनतम चेक-इन", start: "अपना स्कोर बनाने के लिए चेक-इन पूरा करें", noData: "अभी कोई चेक-इन डेटा नहीं", symptoms: "बताए गए लक्षण", safety: "सुरक्षा समीक्षा", voice: "वॉइस इनपुट", checkins: "चेक-इन", consistency: "निरंतरता", trend: "स्वास्थ्य रुझान", personalScore: "व्यक्तिगत चेक-इन स्कोर", insight: "चेक-इन जानकारी", noTrend: "अभी कोई चेक-इन नहीं है। पहले चेक-इन के बाद रुझान दिखाई देगा।", how: "यह कैसे काम करता है", assisted: "AI-सहायता प्राप्त चेक-इन जानकारी", baseline: "व्यक्तिगत आधाररेखा से तुलना। यह चिकित्सीय माप या निदान नहीं है।" },
};
const wellnessInsightCopy: Record<string, Record<string, string>> = {
  en: { change: "from the prior check-in", followUp: "Needs prompt follow-up based on the latest check-in", stable: "Consistent with your recent reported symptoms", on: "On", improved: "Your latest symptom report is stable or improved from the prior check-in.", changed: "Your latest symptom report has changed from the prior check-in; review the suggested action.", confidence: "Extraction confidence", used: "What is used", usedDetail: "Symptoms, reported severity, check-in frequency, and the safety evaluation.", notAssumed: "What is not assumed", notAssumedDetail: "We do not infer vitals, cognitive status, mood, or voice changes unless you record them.", compared: "The score compares a person's own reported check-ins, not generic health thresholds." },
  es: { change: "desde el registro anterior", followUp: "Se recomienda seguimiento pronto según el último registro", stable: "Coincide con sus síntomas recientes informados", on: "Sí", improved: "Su último informe de síntomas está estable o mejoró desde el registro anterior.", changed: "Su último informe de síntomas cambió desde el registro anterior; revise la acción sugerida.", confidence: "Confianza de extracción", used: "Qué se utiliza", usedDetail: "Síntomas, gravedad informada, frecuencia de registros y la evaluación de seguridad.", notAssumed: "Qué no se supone", notAssumedDetail: "No inferimos signos vitales, estado cognitivo, ánimo ni cambios de voz a menos que usted los registre.", compared: "La puntuación compara sus propios registros, no umbrales de salud generales." },
  pt: { change: "desde o registro anterior", followUp: "Precisa de acompanhamento rápido com base no último registro", stable: "Condiz com seus sintomas relatados recentemente", on: "Sim", improved: "Seu último relato de sintomas está estável ou melhorou desde o registro anterior.", changed: "Seu último relato de sintomas mudou desde o registro anterior; reveja a ação sugerida.", confidence: "Confiança da extração", used: "O que é usado", usedDetail: "Sintomas, gravidade relatada, frequência de registros e avaliação de segurança.", notAssumed: "O que não é presumido", notAssumedDetail: "Não inferimos sinais vitais, estado cognitivo, humor ou mudanças na voz sem que você os registre.", compared: "A pontuação compara seus próprios registros, não limites gerais de saúde." },
  zh: { change: "与上次记录相比", followUp: "根据最新记录，建议尽快跟进", stable: "与您近期报告的症状一致", on: "已开启", improved: "您最新的症状报告稳定或较上次记录有所改善。", changed: "您最新的症状报告较上次记录有变化；请查看建议的行动。", confidence: "提取置信度", used: "已使用的信息", usedDetail: "症状、报告的严重程度、记录频率和安全评估。", notAssumed: "未作出的假设", notAssumedDetail: "除非您记录了它们，否则我们不会推断生命体征、认知状态、情绪或声音变化。", compared: "该评分比较的是个人报告的记录，而不是一般健康阈值。" },
  hi: { change: "पिछले चेक-इन से", followUp: "नवीनतम चेक-इन के आधार पर जल्द फ़ॉलो-अप की ज़रूरत है", stable: "आपके हाल में बताए गए लक्षणों के अनुरूप", on: "चालू", improved: "आपकी नवीनतम लक्षण रिपोर्ट स्थिर है या पिछले चेक-इन से बेहतर है।", changed: "आपकी नवीनतम लक्षण रिपोर्ट पिछले चेक-इन से बदली है; सुझाया गया कदम देखें।", confidence: "निकालने का विश्वास", used: "क्या उपयोग किया जाता है", usedDetail: "लक्षण, बताई गई गंभीरता, चेक-इन की आवृत्ति और सुरक्षा मूल्यांकन।", notAssumed: "क्या नहीं माना जाता", notAssumedDetail: "जब तक आप दर्ज नहीं करते, हम महत्वपूर्ण संकेतों, संज्ञानात्मक स्थिति, मनोदशा या आवाज़ में बदलाव का अनुमान नहीं लगाते।", compared: "स्कोर व्यक्ति के अपने बताए चेक-इन की तुलना करता है, सामान्य स्वास्थ्य सीमाओं की नहीं।" },
};
function WellnessOverview({ checkins, evaluation }: { checkins: CheckIn[]; evaluation: CheckInResponse["evaluation"] | null }) {
  const { i18n } = useTranslation();
  const copy = wellnessCopy[i18n.language] || wellnessCopy.en;
  const insightCopy = wellnessInsightCopy[i18n.language] || wellnessInsightCopy.en;
  const [range, setRange] = useState<7 | 14 | 30>(14);
  const [hoveredPoint, setHoveredPoint] = useState<number | null>(null);
  const scores = [...checkins].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()).map(checkinWellnessScore);
  const points = range === 7 ? scores.slice(-7) : range === 30 ? scores.slice(-30) : scores.slice(-14);
  const selected = hoveredPoint === null ? points.length - 1 : hoveredPoint;
  const currentScore = points[points.length - 1];
  const previousScore = points[points.length - 2] || currentScore;
  const change = currentScore && previousScore ? Math.round(((currentScore - previousScore) / previousScore) * 100) : 0;
  const coordinates = points.map((score, index) => `${(index / Math.max(1, points.length - 1)) * 100},${100 - ((score - 35) / 65) * 100}`).join(" ");
  const latest = checkins[0];
  const consistency = Math.min(100, checkins.length * 14);
  const severitySignal = latest?.symptoms.length ? Math.max(0, 100 - Math.round(latest.symptoms.reduce((total, symptom) => total + (symptom.severity ?? 2), 0) / latest.symptoms.length) * 10) : null;
  return <section className="wellness-overview" aria-labelledby="wellness-title">
    <div className="wellness-heading">
      <div><p className="eyebrow">{copy.overview}</p><h2 id="wellness-title">{copy.title}</h2></div>
      <p className="wellness-disclaimer">{copy.disclaimer}</p>
    </div>
    <div className="wellness-grid">
      <section className="wellness-score-card"><p>{copy.latest}</p><strong>{currentScore ?? "—"} {currentScore && <small>/ 100</small>}</strong><span>{currentScore ? `${change >= 0 ? "↑" : "↓"} ${Math.abs(change)}% ${insightCopy.change}` : copy.start}</span><p className="wellness-status">{currentScore ? evaluation?.level && evaluation.level >= 3 ? insightCopy.followUp : insightCopy.stable : copy.noData}</p></section>
      <section className="wellness-signals" aria-label={copy.overview}><div><span>{copy.symptoms}</span><strong>{severitySignal ?? "—"}</strong></div><div><span>{copy.safety}</span><strong>{evaluation ? Math.max(0, 100 - evaluation.level * 18) : "—"}</strong></div><div><span>{copy.voice}</span><strong>{latest?.source === "voice" ? insightCopy.on : "—"}</strong></div><div><span>{copy.checkins}</span><strong>{checkins.length}</strong></div><div><span>{copy.consistency}</span><strong>{checkins.length ? consistency : "—"}</strong></div></section>
    </div>
    <section className="wellness-chart-card"><div className="wellness-chart-heading"><div><h3>{copy.trend}</h3><p>{copy.personalScore} — {range} days</p></div><div className="trend-range" aria-label={copy.trend}><button className={range === 7 ? "selected" : ""} onClick={() => setRange(7)}>7</button><button className={range === 14 ? "selected" : ""} onClick={() => setRange(14)}>14</button><button className={range === 30 ? "selected" : ""} onClick={() => setRange(30)}>30</button></div></div>{points.length ? <><div className="chart-wrap"><svg viewBox="0 0 100 100" role="img" aria-label={`${copy.title} ${points[selected]}`} preserveAspectRatio="none"><polyline points={coordinates} /></svg><div className="chart-points">{points.map((score, index) => <button key={`${score}-${index}`} style={{ left: `${(index / Math.max(1, points.length - 1)) * 100}%`, bottom: `${((score - 35) / 65) * 100}%` }} onMouseEnter={() => setHoveredPoint(index)} onFocus={() => setHoveredPoint(index)} aria-label={`${copy.checkins} ${index + 1}: ${score}`} />)}</div><p className="chart-tooltip">{copy.checkins} {selected + 1}: <strong>{points[selected]}</strong></p></div><p className="ai-insight"><strong>{copy.insight}:</strong> {change >= 0 ? insightCopy.improved : insightCopy.changed}</p></> : <p className="muted chart-empty">{copy.noTrend}</p>}</section>
    <section className="ai-insights"><div className="section-heading"><div><p className="eyebrow">{copy.how}</p><h3>✨ {copy.assisted}</h3></div><span>{evaluation ? `${insightCopy.confidence} ${Math.round(evaluation.confidence * 100)}%` : copy.noData}</span></div><div className="insight-grid"><article><h4>{insightCopy.used}</h4><p>{insightCopy.usedDetail}</p></article><article><h4>{insightCopy.notAssumed}</h4><p>{insightCopy.notAssumedDetail}</p></article><article><h4>{copy.baseline}</h4><p>{insightCopy.compared}</p></article></div><p className="baseline-note">{copy.baseline}</p></section>
  </section>;
}

function Dashboard({
  senior,
  plan,
  onSignOut,
  onProfileSave,
}: {
  senior: Senior;
  plan: CarePlan;
  onSignOut: () => void;
  onProfileSave: (updated: Senior) => Promise<void>;
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
  const [editingPlan, setEditingPlan] = useState(false);
  const [routineDraft, setRoutineDraft] = useState(plan.routines.join(", "));
  const [instructionDraft, setInstructionDraft] = useState(plan.instructions.join(", "));
  const [appointmentDraft, setAppointmentDraft] = useState({ title: "", date: "", location: "" });
  const [followUpKey, setFollowUpKey] = useState<FollowUpKey | null>(null);
  const [followUpForId, setFollowUpForId] = useState<string | null>(null);
  const [followUpRecords, setFollowUpRecords] = useState<Record<string, FollowUpRecord[]>>(() => savedValue<Record<string, FollowUpRecord[]>>(`carepath-followups:${senior.id}`) || {});
  const [downloadingHandoff, setDownloadingHandoff] = useState(false);
  const [reminderKind, setReminderKind] = useState<"meds" | "appointment" | "refill" | "caregiver_update">("meds");
  const [reminderMedicationId, setReminderMedicationId] = useState("");
  const [reminderRecipient, setReminderRecipient] = useState<"self" | "caregiver" | "both">("self");
  const [reminderAt, setReminderAt] = useState("");
  const [scheduledReminders, setScheduledReminders] = useState<ReminderJob[]>([]);
  const [sendingReminder, setSendingReminder] = useState(false);
  const [reminderStatus, setReminderStatus] = useState("");
  const [sharingConversation, setSharingConversation] = useState(false);
  const [shareStatus, setShareStatus] = useState("");
  const [expandedCheckin, setExpandedCheckin] = useState<string | null>(null);
  const [checkinDetails, setCheckinDetails] = useState<Record<string, CheckInResponse>>({});
  const [handoffDetails, setHandoffDetails] = useState<Record<string, HandoffPacket>>({});
  const [speaking, setSpeaking] = useState(false);
  const languageLoaded = useRef(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const messageRef = useRef("");
  const profileRegistered = useRef(false);
  const guidanceTone = evaluation && (evaluation.level >= 4 ? "emergency" : evaluation.level >= 3 ? "urgent" : evaluation.level === 2 ? "watch" : "calm");
  const symptomCounts = checkins.flatMap((checkin) => checkin.symptoms.map((symptom) => symptom.label)).reduce<Record<string, number>>((counts, label) => ({ ...counts, [label]: (counts[label] || 0) + 1 }), {});
  const highestSymptomCount = Math.max(1, ...Object.values(symptomCounts));
  const followUpAnswerIds = new Set(Object.values(followUpRecords).flatMap((records) => records.map((record) => record.checkinId)));
  const historyCheckins = checkins.filter((checkin) => !followUpAnswerIds.has(checkin.id));
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
        setScheduledReminders(await api.reminders(senior.id));
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
    localStorage.setItem(`carepath-followups:${senior.id}`, JSON.stringify(followUpRecords));
  }, [followUpRecords, senior.id]);
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
    setShareStatus("");
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
      if (askedKey && followUpForId) {
        setFollowUpRecords((current) => ({
          ...current,
          [followUpForId]: [...(current[followUpForId] || []), { question: followUpText(askedKey, i18n.language), answer: text.trim(), checkinId: result.checkin.id }],
        }));
      }
      if (askedKey === "back-severity") {
        setFollowUpKey("back-warning-signs");
      } else if (askedKey === "back-warning-signs") {
        setFollowUpKey(null);
        setFollowUpForId(null);
      } else if (result.checkin.symptoms.some((symptom) => symptom.label === "back pain")) {
        setFollowUpKey("back-severity");
        setFollowUpForId(result.checkin.id);
      } else {
        setFollowUpKey(null);
        setFollowUpForId(null);
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
      const result = await api.sendReminder(senior.id, reminderKind, i18n.language, reminderRecipient);
      const recipients = result.recipients?.length ? ` to ${result.recipients.length} phone${result.recipients.length === 1 ? "" : "s"}` : ` to ${result.recipient || "the care circle"}`;
      setReminderStatus(result.ok ? `${result.mocked ? "Demo message queued" : "Message sent"}${recipients}. Reply DONE is expected.` : (result.error || "Message was not sent."));
    } catch (reason) {
      setReminderStatus(reason instanceof Error ? reason.message : "Message was not sent.");
    } finally {
      setSendingReminder(false);
    }
  };
  const shareConversation = async () => {
    const latestCheckin = checkins[0];
    if (!latestCheckin || sharingConversation) return;
    setSharingConversation(true);
    setShareStatus("");
    try {
      const receipt = await api.shareCheckinWithCaregiver(latestCheckin.id);
      setShareStatus(receipt.status === "mocked" ? "Demo update queued for your caregiver." : receipt.status === "sent" ? "Update sent to your caregiver." : "The caregiver update could not be sent.");
    } catch (reason) {
      setShareStatus(reason instanceof Error ? reason.message : "The caregiver update could not be sent.");
    } finally {
      setSharingConversation(false);
    }
  };
  const scheduleReminder = async () => {
    if (!reminderAt) {
      setReminderStatus("Choose a date and time for the reminder.");
      return;
    }
    setSendingReminder(true);
    setReminderStatus("");
    try {
      const job = await api.scheduleReminder({
        senior_id: senior.id,
        kind: reminderKind,
        language: i18n.language,
        recipient: reminderRecipient,
        scheduled_for: new Date(reminderAt).toISOString(),
      });
      setScheduledReminders((current) => [...current, job].sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for)));
      setReminderAt("");
      setReminderStatus("Reminder scheduled. It will be sent to the selected phone by LINQ.");
    } catch (reason) {
      setReminderStatus(reason instanceof Error ? reason.message : "Reminder could not be scheduled.");
    } finally {
      setSendingReminder(false);
    }
  };
  const cancelScheduledReminder = async (reminderId: string) => {
    try {
      const job = await api.cancelReminder(senior.id, reminderId);
      setScheduledReminders((current) => current.map((item) => item.id === job.id ? job : item));
    } catch (reason) {
      setReminderStatus(reason instanceof Error ? reason.message : "Reminder could not be cancelled.");
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
            {t("care")}
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
            <h1 className="moving-greeting">
              {timeGreeting(senior.display_name.split(" ")[0], i18n.language)}
            </h1>
            <p>{t("feeling")}</p>
          </div>
          <section className="checkin-card">
            {followUpKey && (
              <div className="followup-question" role="status">
                <p className="eyebrow">{t("oneMoreQuestion")}</p>
                <h3>{followUpText(followUpKey, i18n.language)}</h3>
                <p className="helper-text">{t("answerHelps")}</p>
              </div>
            )}
            <h2>{followUpKey ? t("yourAnswer") : t("tell")}</h2>
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
                {saving ? t("saving") : t("check")} <ArrowRight size={20} />
              </button>
            </div>
            {listening && <p className="live-transcript" role="status">{t("listening")}</p>}
            {error && <p className="form-error" role="alert">{error}</p>}
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
              }}>{speaking ? t("speaking") : `🔊 ${t("readGuidance")}`}</button>
              <button className="secondary-button" type="button" onClick={() => void shareConversation()} disabled={sharingConversation || !checkins[0]}>
                {sharingConversation ? t("sending") : t("sendToCaregiver")}
              </button>
              {evaluation.level >= 3 && <button className="secondary-button" type="button" onClick={() => void downloadHandoff()} disabled={downloadingHandoff}><Download size={18} /> {downloadingHandoff ? t("preparingHandoff") : t("downloadHandoff")}</button>}
              {shareStatus && <p className="form-success" role="status">{shareStatus}</p>}
            </div>
          </section>}
          {evaluation?.risk && <RiskPanel risk={evaluation.risk} />}
        </main>
      )}
      {page === "plan" && (
        <main className="dashboard">
          <h1 className="care-page-title">{t("care")}</h1>
          <WellnessOverview checkins={checkins} evaluation={evaluation} />
          <div className="page-title-row">
            <h2>{t("plan")}</h2>
            <button className="secondary-button compact-button" type="button" onClick={() => setEditingPlan((editing) => !editing)}>{editingPlan ? t("doneEditing") : t("editCarePlan")}</button>
          </div>
          {editingPlan && <section className="checkin-card care-plan-editor">
            <label>{t("dailyHabits")}</label><textarea value={routineDraft} onChange={(event) => setRoutineDraft(event.target.value)} />
            <label>{t("instructions")}</label><textarea value={instructionDraft} onChange={(event) => setInstructionDraft(event.target.value)} />
            <h2>{t("addAppointment")}</h2>
            <div className="form-grid"><div><label htmlFor="appointment-title">{t("appointment")}</label><input id="appointment-title" value={appointmentDraft.title} onChange={(event) => setAppointmentDraft((current) => ({ ...current, title: event.target.value }))} /></div><div><label htmlFor="appointment-date">{t("dateTime")}</label><input id="appointment-date" type="datetime-local" value={appointmentDraft.date} onChange={(event) => setAppointmentDraft((current) => ({ ...current, date: event.target.value }))} /></div><div className="full"><label htmlFor="appointment-location">{t("location")}</label><input id="appointment-location" value={appointmentDraft.location} onChange={(event) => setAppointmentDraft((current) => ({ ...current, location: event.target.value }))} /></div></div>
            <button className="primary-button" type="button" onClick={() => { plan.routines = splitList(routineDraft); plan.instructions = splitList(instructionDraft); if (appointmentDraft.title.trim()) plan.appointments = [...plan.appointments, { id: crypto.randomUUID(), title: appointmentDraft.title.trim(), date: appointmentDraft.date, location: appointmentDraft.location.trim() }]; localStorage.setItem(`carepath-plan:${senior.id}`, JSON.stringify(plan)); setAppointmentDraft({ title: "", date: "", location: "" }); setEditingPlan(false); }}>{t("saveCarePlan")}</button>
          </section>}
          <PlanSection title={t("dailyRoutine")} values={plan.routines} />
          <PlanSection title={t("instructions")} values={plan.instructions} />
          <PlanSection
            title={t("appointment")}
            values={plan.appointments.map(
              (item) => `${item.title} · ${item.date}`,
            )}
          />
          <section className="reminder-card">
            <div>
              <p className="eyebrow">{t("textReminders")}</p>
              <h2>{t("sendTextReminder")}</h2>
              <p className="helper-text">{t("reminderDescription")}</p>
            </div>
            <div className="reminder-actions">
              <label className="sr-only" htmlFor="reminder-kind">{t("reminderType")}</label>
              <select id="reminder-kind" value={reminderKind} onChange={(event) => setReminderKind(event.target.value as typeof reminderKind)}>
                <option value="meds">{t("medication")}</option>
                <option value="appointment">{t("appointment")}</option>
                <option value="refill">{t("refill")}</option>
              </select>
              <label className="sr-only" htmlFor="reminder-medication">{t("medications")}</label>
              <select id="reminder-medication" value={reminderMedicationId || senior.medications[0]?.id || ""} onChange={(event) => setReminderMedicationId(event.target.value)} disabled={!senior.medications.length || reminderKind !== "meds"}>
                {senior.medications.length ? senior.medications.map((medication) => <option key={medication.id} value={medication.id}>{medication.name}</option>) : <option value="">{t("addMedicationFirst")}</option>}
              </select>
              <label className="sr-only" htmlFor="reminder-recipient">{t("reminderRecipient")}</label>
              <select id="reminder-recipient" value={reminderRecipient} onChange={(event) => setReminderRecipient(event.target.value as "self" | "caregiver" | "both")}>
                <option value="self">{t("me")}</option>
                <option value="caregiver" disabled={!senior.caregivers?.[0]}>{t("caregiver")}</option>
                <option value="both" disabled={!senior.caregivers?.[0]}>{t("meAndCaregiver")}</option>
              </select>
              <button className="secondary-button" type="button" onClick={() => void sendReminder()} disabled={sendingReminder || (reminderKind === "meds" && !senior.medications.length)}>
                {sendingReminder ? t("sending") : t("sendText")}
              </button>
              <label className="sr-only" htmlFor="reminder-at">Reminder date and time</label>
              <input id="reminder-at" type="datetime-local" value={reminderAt} min={new Date(Date.now() + 60000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)} onChange={(event) => setReminderAt(event.target.value)} />
              <button className="primary-button" type="button" onClick={() => void scheduleReminder()} disabled={sendingReminder || !reminderAt || (reminderKind === "meds" && !senior.medications.length)}>
                {sendingReminder ? t("scheduling") : t("scheduleReminder")}
              </button>
            </div>
            {reminderStatus && <p className="form-success" role="status">{reminderStatus}</p>}
            {scheduledReminders.length > 0 && <div className="scheduled-reminders">
              <h3>{t("scheduledReminders")}</h3>
              {scheduledReminders.map((job) => <div className="scheduled-reminder" key={job.id}>
                <span><strong>{job.kind === "meds" ? t("medication") : job.kind === "appointment" ? t("appointment") : t("refill")}</strong> · {new Date(job.scheduled_for).toLocaleString(i18n.language)} · {t(`reminderAudience.${job.recipient}`)}</span>
                {job.status === "scheduled" ? <button className="text-button" type="button" onClick={() => void cancelScheduledReminder(job.id)}>{t("cancel")}</button> : <small>{t(`reminderStatus.${job.status}`, { defaultValue: job.status })}</small>}
              </div>)}
            </div>}
          </section>
        </main>
      )}
      {page === "history" && (
        <main className="dashboard">
          <div className="page-title-row page-heading"><h1>{t("history")}</h1></div>
          <section className="symptom-trends" aria-label={t("recurringSymptoms")}>
            <h2>{t("recurringSymptoms")}</h2>
            {Object.keys(symptomCounts).length ? Object.entries(symptomCounts).sort(([, a], [, b]) => b - a).map(([label, count]) => <div className="trend-row" key={label}><span>{t(`symptom.${label}`, { defaultValue: label })}</span><div className="trend-track"><div style={{ width: `${(count / highestSymptomCount) * 100}%` }} /></div><strong>{count}</strong></div>) : <p className="muted">{t("recurringSymptomsEmpty")}</p>}
          </section>
          {historyCheckins.map((checkin) => (
            <article className="history-record" key={checkin.id}>
              <button className="history-record-button" type="button" onClick={() => void openCheckin(checkin)} aria-expanded={expandedCheckin === checkin.id}>
                <strong>{checkin.symptoms.map((symptom) => t(`symptom.${symptom.label}`, { defaultValue: symptom.label })).join(", ") || t("check")}</strong>
                <p>{checkin.created_at}</p>
                <p>{displayCheckinText(checkin.raw_text, i18n.language)}</p>
              </button>
              {expandedCheckin === checkin.id && checkinDetails[checkin.id] && (
                <div className="history-detail">
                  <h2>{t("providerSummary")}</h2>
                  <p>{displayCheckinText(checkin.raw_text, i18n.language)}</p>
                  {followUpRecords[checkin.id]?.map((record, index) => <p key={`${record.question}-${index}`}><strong>{record.question}</strong><br />{record.answer}</p>)}
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
          onSave={onProfileSave}
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
      onProfileSave={async (updated) => {
        await api.updateSenior(updated);
        localStorage.setItem(`carepath-profile:${updated.id}`, JSON.stringify(updated));
        setSenior(updated);
      }}
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
