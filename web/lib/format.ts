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
