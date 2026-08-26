// Turkish UI labels + small display helpers.

import type { InfoStatus, RequirementType, RunPhase, Verdict } from "@/lib/types";

export const TYPE_LABEL: Record<RequirementType, string> = {
  hard: "Zorunlu",
  scored: "Puanlanır",
  bonus: "Avantaj",
  penalty: "Eksi puan",
  disqualifier: "Eleyici",
  info: "Bilgi",
};

export const IMPORTANCE_LABEL: Record<string, string> = {
  critical: "Kritik",
  high: "Yüksek",
  medium: "Orta",
  low: "Düşük",
};

export const EVALUATOR_LABEL: Record<string, string> = {
  deterministic: "Kural",
  semantic: "AI değerlendirme",
  hybrid: "Kural + AI",
};

export const CATEGORY_LABEL: Record<string, string> = {
  relevant_experience: "İlgili deneyim",
  skills: "Beceriler",
  industry: "Sektör",
  career_stability: "Kariyer istikrarı",
  education: "Eğitim",
  language: "Dil",
  location: "Lokasyon",
  custom: "Özel kriterler",
};

export const VERDICT_ICON: Record<Verdict, string> = {
  met: "✓",
  partially_met: "◐",
  not_met: "✗",
  unknown: "?",
  disqualified: "⛔",
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  met: "Karşılıyor",
  partially_met: "Kısmen",
  not_met: "Karşılamıyor",
  unknown: "Bilgi yok",
  disqualified: "Elendi",
};

export const INFO_STATUS_LABEL: Record<InfoStatus, string> = {
  explicit: "CV'de yazıyor",
  inferred: "Çıkarım",
  ambiguous: "Belirsiz",
  missing: "Bilgi yok",
};

export const BAND_LABEL: Record<string, string> = {
  top: "En iyi",
  strong: "Güçlü",
  possible: "Olası",
  weak: "Zayıf",
  rejected: "Elendi",
};

export const SPEC_STATUS_LABEL: Record<string, string> = {
  compiling: "Derleniyor…",
  draft: "Taslak — onayınızı bekliyor",
  confirmed: "Onaylı",
  superseded: "Eski sürüm",
  failed: "Derleme hatası",
};

export const RUN_STATUS_LABEL: Record<RunPhase, string> = {
  queued: "Kuyrukta",
  screening: "Adaylar değerlendiriliyor",
  selecting: "Derin analiz seçimi",
  deep_analysis: "Derin analiz",
  scoring: "Puanlama ve sıralama",
  complete: "Tamamlandı",
  failed: "Hata",
  cancelled: "İptal edildi",
};

export const PARSE_STATUS_LABEL: Record<string, string> = {
  pending: "Bekliyor",
  parsing: "Okunuyor",
  parsed: "Profil çıkarıldı",
  failed: "Hata",
  unsupported: "Desteklenmiyor",
};

export const REVIEW_REASON_LABEL: Record<string, string> = {
  low_confidence: "Düşük güven skoru",
  hard_requirement_unverified: "Zorunlu kriter doğrulanamadı",
  disqualifier_triggered: "Eleyici kriter tetiklendi",
  light_screen_failed: "Ön değerlendirme hatası — kural sonuçlarıyla devam edildi",
  deep_analysis_failed: "Derin analiz hatası — ön değerlendirme korundu",
  prompt_injection_detected:
    "CV, değerlendirmeyi yönlendirmeye çalışan metin içeriyor — puana etki etmedi",
  deep_vote_disagreement:
    "Derin analiz oyları uyuşmadı — sınır adayı, insan kontrolü önerilir",
};

export const INJECTION_KIND_LABEL: Record<string, string> = {
  instruction_override: "Önceki talimatları yoksaymaya çalışıyor",
  fake_system_prompt: "Sahte sistem talimatı",
  score_demand: "Doğrudan puan talebi",
  evaluation_bypass: "Değerlendirmeyi atlatma girişimi",
  competitor_attack: "Diğer adayları eleme talebi",
  role_play: "Değerlendiriciye rol biçme",
};

export const PARSE_ERROR_LABEL: Record<string, string> = {
  encrypted_pdf: "Şifreli PDF — parola kaldırıp yeniden yükleyin",
  file_too_large: "Dosya çok büyük",
  unsupported_type: "Desteklenmeyen dosya türü (PDF bekleniyor)",
  multi_person_document: "Birden fazla kişinin CV'si — dosyayı ayırıp yeniden yükleyin",
};

export const SENIORITY_LABEL: Record<string, string> = {
  junior: "Başlangıç",
  mid: "Orta düzey",
  senior: "Kıdemli",
  lead: "Yönetici",
  unknown: "—",
};

/** Back-translations sometimes already begin with the label we render — dedupe. */
export function stripUnderstoodPrefix(text: string): string {
  return text.replace(/^\s*Anladığımız\s*:\s*/i, "");
}

export function confidenceLabel(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 0.75) return "Yüksek";
  if (value >= 0.55) return "Orta";
  return "Düşük";
}

export function months(value: number | null | undefined): string {
  if (value == null) return "—";
  const years = Math.floor(value / 12);
  const rest = value % 12;
  if (years === 0) return `${rest} ay`;
  return rest === 0 ? `${years} yıl` : `${years} yıl ${rest} ay`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" });
}

export function bandClass(band: string | null | undefined): string {
  switch (band) {
    case "top":
      return "band band-top";
    case "strong":
      return "band band-strong";
    case "possible":
      return "band band-possible";
    case "rejected":
      return "band band-rejected";
    default:
      return "band band-weak";
  }
}

export const STAGE_LABEL: Record<string, string> = {
  light: "ön değerlendirme",
  deep: "derin analiz",
  compile: "kriter derleme",
  extract: "CV okuma",
};

/** Estimated spend, in the currency the model provider bills in. */
export function usd(amount: number): string {
  return `$${amount < 0.01 ? amount.toFixed(4) : amount.toFixed(2)}`;
}

export const PIPELINE_STAGE_LABEL: Record<string, string> = {
  new: "Yeni",
  shortlisted: "Kısa liste",
  contacted: "Temas kuruldu",
  interviewing: "Görüşme",
  offer: "Teklif",
  hired: "İşe alındı",
  dropped: "Olumsuz",
};

export const EVENT_KIND_LABEL: Record<string, string> = {
  stage_change: "Aşama değişti",
  note: "Not",
  contact: "Temas",
  meeting: "Görüşme",
  outcome: "Sonuç",
};

export function scoreText(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value)}`;
}

/** Predicate fields, in the words HR uses. */
export const FIELD_LABEL: Record<string, string> = {
  "derived.total_experience_months": "Toplam deneyim",
  "derived.job_count": "İş sayısı",
  "derived.avg_tenure_months": "Ortalama görev süresi",
  "derived.job_changes_last_5y": "Son 5 yılda iş değişikliği",
  "derived.max_employment_gap_months": "En uzun kariyer arası",
  "derived.employment_gap_count": "Kariyer arası sayısı",
  "derived.highest_degree_rank": "En yüksek eğitim",
  "education.highest_degree_rank": "En yüksek eğitim",
  "derived.current_employment_status": "Çalışma durumu",
  "derived.seniority_estimate": "Kıdem",
  "location.city_canonical": "Şehir",
  "location.country": "Ülke",
  industries: "Sektörler",
  tools_technologies: "Araçlar / teknolojiler",
  "skills.canonical": "Beceriler",
  "certifications.name_canonical": "Sertifikalar",
  "experience.title_canonical": "Unvanlar",
};

const DEGREE_NAME = ["—", "Lise", "Ön lisans", "Lisans", "Yüksek lisans", "Doktora"];
const CEFR_NAME = ["—", "A1", "A2", "B1", "B2", "C1", "C2"];
const OP_LABEL: Record<string, string> = {
  ">=": "en az",
  ">": "daha fazla",
  "<=": "en fazla",
  "<": "daha az",
  "==": "eşit",
  "!=": "farklı",
  in: "şunlardan biri",
  not_in: "şunlardan biri değil",
  contains: "içermeli",
  exists: "belirtilmiş olmalı",
};

export function fieldLabel(field: string): string {
  const language = field.match(/^languages\['([a-z]{2,3})'\]\.cefr_rank$/);
  if (language) {
    const names: Record<string, string> = { en: "İngilizce", de: "Almanca", fr: "Fransızca" };
    return `${names[language[1]] ?? language[1].toUpperCase()} seviyesi`;
  }
  return FIELD_LABEL[field] ?? field;
}

function fieldValue(field: string, value: unknown): string {
  if (value == null) return "belirtilmemiş";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "yok";
  if (field.endsWith("_months") && typeof value === "number") return months(value);
  if (field.includes("degree_rank") && typeof value === "number") {
    return DEGREE_NAME[value] ?? String(value);
  }
  if (field.includes("cefr_rank") && typeof value === "number") {
    return CEFR_NAME[value] ?? String(value);
  }
  return String(value);
}

/** "Toplam deneyim: 7 yıl 2 ay (aranan: en az 3 yıl)" — what the rule read. */
export function ruleEvidence(e: {
  field?: string;
  observed?: unknown;
  expected?: { op?: string; value?: unknown };
  present?: boolean;
}): string {
  if (!e.field) return "";
  const label = fieldLabel(e.field);
  const found = e.present ? fieldValue(e.field, e.observed) : "CV'de belirtilmemiş";
  const op = e.expected?.op ? OP_LABEL[e.expected.op] ?? e.expected.op : null;
  const wanted =
    e.expected?.value !== undefined && op
      ? ` (aranan: ${op} ${fieldValue(e.field, e.expected.value)})`
      : "";
  return `${label}: ${found}${wanted}`;
}
