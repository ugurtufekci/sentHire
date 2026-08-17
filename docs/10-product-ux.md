# 10 — Product UX: The HR Experience

Everything happens in the browser on our SaaS — sign up, invite the team, screen, hire.
No installation, no IT project. The UX principle:

> **HR describes what they are looking for in natural language; the system converts
> that intent into a structured, explainable screening system — and always shows its
> work.**

## 1. Onboarding (first 10 minutes)

1. Sign up with work email → create organization → invite teammates (owner/recruiter/
   viewer roles).
2. Guided first job: pick a template, paste requirements, drag in a folder of CVs.
3. First ranked list within minutes — the product sells itself on run #1.

## 2. Create a job: template + natural language

- **Template gallery** ("Satış Uzmanı", "Yazılım Geliştirici", …) → picking one fills
  the requirement panel with editable cards (label, type badge, importance, threshold,
  weight).
- **Natural-language box** beneath: *"Aradığınız adayı kendi cümlelerinizle anlatın…"*
  HR types freely (TR/EN). On *Analyze*, compiled requirements appear as new cards,
  each showing **the original sentence it came from** and a type badge the user can
  flip (Zorunlu / Puanlanır / Avantaj / Eksi puan).
- **Clarifying questions inline**: "Çok sık iş değiştirme — kaç değişiklik 'çok sık'?
  (varsayılan: 5 yılda 3+)" with an editable default.
- **Back-translation strip**: "Anladığımız: Ankara'da ikamet tercih sebebi; en az 3 yıl
  B2B satış deneyimi zorunlu; …" — the user confirms the *interpretation*, not just the
  JSON (doc 04 §7).
- Compliance nudges appear here too: a blocked criterion shows *why* it can't be used
  and, where possible, a lawful reformulation (doc 09 §2).
- **Confirm requirements** freezes spec v1. A weights drawer shows the category split
  with sliders (defaults visible, sum auto-normalized).

## 3. Upload CVs

Drag-and-drop up to ~500 files (or a zip); per-file states stream live: *yüklendi →
okunuyor → profil çıkarıldı / hata (nedeniyle)*. Duplicates are labeled ("bu CV zaten
yüklenmiş — profil yeniden kullanılacak"). Non-CV files are set aside, not silently
dropped. Nothing blocks: 3 failed files never stop 97 good ones.

## 4. Run screening & watch the funnel

One button: **Taramayı Başlat** — with a mode choice when volume is high:
*Hızlı (interactive, ~dakikalar)* vs *Ekonomik (batch, ~1 saat, %50 ucuz)* and a cost
preview ("~₺X — tahmini"). Then a live funnel:

```text
100 CV → 98 profil → 72 ön filtre ✓ → 72 değerlendirildi → 15 derin analiz → sıralandı
                     26 elendi (nedenleriyle)                       [==========78%   ]
```

Each funnel segment is clickable → the candidates in that state, with reasons.

## 5. Results: ranked, explained, actionable

**Ranked table**: rank, name (or masked ID in blind mode), score with band color,
hard-requirements ✓/✗, confidence chip, top strength/weakness snippets, flags
(`needs_review`, `low extraction confidence`, `integrity_review`). Filters: band,
pass/fail, flags, requirement-level filters ("SaaS bonus alanlar").

**Candidate detail** (the trust-building screen):
- Score breakdown exactly as computed (doc 06): category bars × weights, itemized
  bonuses/penalties — "neden 84?" is visible arithmetic.
- Requirement-by-requirement list: verdict icon, evidence quote, `info_status` chip
  (*CV'de yazıyor* / *çıkarım* / *belirsiz* / *bilgi yok*).
- **Split view with the original CV**: clicking any evidence quote scrolls/highlights
  the exact span in the PDF viewer. This single interaction converts skeptics.
- Strengths / weaknesses / missing-information panels; AI-generated summaries are
  labeled as such.

**Rejected view**: every eliminated candidate with the specific gate(s) failed and the
evidence (or the absence) behind it — "why was X rejected" is never unanswerable.

## 6. Human control: overrides & review queue

- Any candidate can be promoted to shortlist or rejected regardless of AI output —
  one click + optional reason; recorded in the audit trail and shown as a human badge
  on the card.
- `needs_review` queue: low-confidence and flagged candidates wait for a human glance
  before they can be bulk-actioned; the system never buries uncertainty.
- Override analytics loop: "Bu gereksinimde 6 kez elle düzeltme yaptınız — eşiği
  güncellemek ister misiniz?" (mis-specified rules get surfaced, doc 09 §1).

## 7. Iterate: change criteria without re-processing

Editing requirements/weights creates spec v2 with an impact preview **before running**:

```text
Değişiklik: "Almanca avantaj" eklendi; Lokasyon ağırlığı %5 → %10
Etki: 72 aday yeniden puanlanacak · 1 yeni kriter değerlendirilecek
Maliyet: ~₺Y · Süre: ~1 dk        [Yeniden sırala]
```

Weight-only changes apply instantly and free; semantic additions cost cents and a
minute (docs 04 §6, 06 §6). Runs are versioned — v1 vs v2 rankings are comparable
("kim yer değiştirdi?" diff view).

## 8. Compare & decide

Side-by-side matrix (2–5 candidates × requirements) with verdict icons and per-cell
evidence popovers; export of the shortlist + per-candidate reports (PDF) for hiring
managers — export events are audited.

## 9. Trust affordances (cross-cutting)

- Confidence is always visible, in words (Yüksek/Orta/Düşük), not decimals.
- AI-authored text is labeled; quotes from the CV are visually distinct.
- "Bilgi yok" is styled neutrally — the UI never renders missing data as red/negative.
- Cost/time expectations are set before every paid action.
- Empty states teach: the first rejected-candidate view explains gates and overrides.
