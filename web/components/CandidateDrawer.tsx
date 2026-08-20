"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  CATEGORY_LABEL,
  INFO_STATUS_LABEL,
  REVIEW_REASON_LABEL,
  TYPE_LABEL,
  VERDICT_ICON,
  VERDICT_LABEL,
  bandClass,
  BAND_LABEL,
  confidenceLabel,
  INJECTION_KIND_LABEL,
  ruleEvidence,
} from "@/lib/format";
import type { ResultDetail, Verdict } from "@/lib/types";

const CORRECTABLE: Verdict[] = ["met", "partially_met", "not_met", "unknown"];

export default function CandidateDrawer({
  runId,
  applicationId,
  candidateName,
  onClose,
  onCorrected,
}: {
  runId: string;
  applicationId: string;
  candidateName: string | null;
  onClose: () => void;
  /** A correction re-scores and re-ranks the run, so the list behind the
   *  drawer is stale the moment one is saved. */
  onCorrected?: () => void;
}) {
  const [detail, setDetail] = useState<ResultDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  const correct = (reqId: string, verdict: Verdict) => {
    setSaving(true);
    setError(null);
    api
      .overrideVerdict(runId, applicationId, reqId, verdict, reason.trim() || null)
      .then((updated) => {
        setDetail(updated);
        setEditing(null);
        setReason("");
        onCorrected?.();
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setSaving(false));
  };

  useEffect(() => {
    api
      .resultDetail(runId, applicationId)
      .then(setDetail)
      .catch((e) => setError((e as Error).message));
  }, [runId, applicationId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const doc = detail?.result;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Aday değerlendirme detayı">
        <div className="hstack" style={{ justifyContent: "space-between", marginBottom: 4 }}>
          <h2 style={{ margin: 0, fontSize: 19 }}>{candidateName ?? "Aday"}</h2>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Kapat">
            Kapat
          </button>
        </div>

        {error && <div className="notice bad">{error}</div>}
        {!detail && !error && <div className="empty">Yükleniyor…</div>}

        {detail && doc && (
          <div className="stack" style={{ gap: 16 }}>
            <div className="hstack">
              <span className="mono" style={{ fontSize: 26, fontWeight: 650 }}>
                {detail.overall_score != null ? Math.round(detail.overall_score) : "—"}
                <span className="tiny" style={{ fontWeight: 400 }}>
                  /100
                </span>
              </span>
              {detail.band && (
                <span className={bandClass(detail.band)}>{BAND_LABEL[detail.band] ?? detail.band}</span>
              )}
              {detail.rank != null && <span className="chip">Sıra {detail.rank}</span>}
              <span className="chip">Güven: {confidenceLabel(detail.confidence)}</span>
            </div>

            {doc.needs_review && (
              <div className="notice warn">
                İnsan incelemesi önerilir
                {doc.review_reasons.length > 0 && (
                  <>
                    {": "}
                    {doc.review_reasons
                      .map((r) => REVIEW_REASON_LABEL[r] ?? r)
                      .join(" · ")}
                  </>
                )}
              </div>
            )}

            {doc.narrative?.summary && <p style={{ margin: 0 }}>{doc.narrative.summary}</p>}

            {doc.gate.status === "fail" && doc.rejection_reasons && (
              <div className="notice bad">
                <strong>Elenme nedenleri</strong>
                <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                  {doc.rejection_reasons.map((r) => (
                    <li key={r.req_id}>{r.label}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Skor dökümü — puan aritmetiktir, görünür aritmetik */}
            <section>
              <span className="field-label">Skor dökümü</span>
              <div className="card quiet">
                {Object.entries(doc.categories).map(([cat, c]) => (
                  <div key={cat} className="cat-row">
                    <span>{CATEGORY_LABEL[cat] ?? cat}</span>
                    <span className="cat-bar">
                      <span className="cat-fill" style={{ width: `${Math.round(c.score * 100)}%` }} />
                    </span>
                    <span className="mono tiny">
                      {Math.round(c.score * 100)} × %{Math.round(c.weight * 100)}
                    </span>
                  </div>
                ))}
                <hr className="divider" />
                <div className="hstack" style={{ justifyContent: "space-between", fontSize: 13.5 }}>
                  <span>Taban puan</span>
                  <span className="mono">{doc.base_score.toFixed(1)}</span>
                </div>
                {doc.adjustments.map((a, i) => (
                  <div
                    key={i}
                    className="hstack"
                    style={{ justifyContent: "space-between", fontSize: 13.5 }}
                  >
                    <span>{a.kind === "bonus" ? "Avantaj" : "Eksi puan"} · {a.req_id}</span>
                    <span className="mono" style={{ color: a.kind === "bonus" ? "var(--good)" : "var(--bad)" }}>
                      {a.kind === "bonus" ? "+" : "−"}
                      {a.points}
                    </span>
                  </div>
                ))}
                <div
                  className="hstack"
                  style={{ justifyContent: "space-between", fontWeight: 600, marginTop: 4 }}
                >
                  <span>Toplam</span>
                  <span className="mono">{doc.final_score.toFixed(1)}</span>
                </div>
              </div>
            </section>

            {/* Güçlü / gelişime açık yönler */}
            {((doc.narrative?.strengths?.length ?? 0) > 0 ||
              (doc.narrative?.weaknesses?.length ?? 0) > 0) && (
              <section className="stack" style={{ gap: 8 }}>
                {(doc.narrative?.strengths?.length ?? 0) > 0 && (
                  <div>
                    <span className="field-label">Güçlü yönler</span>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {doc.narrative.strengths!.map((s, i) => (
                        <li key={i}>{s}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {(doc.narrative?.weaknesses?.length ?? 0) > 0 && (
                  <div>
                    <span className="field-label">Dikkat edilecek noktalar</span>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {doc.narrative.weaknesses!.map((s, i) => (
                        <li key={i}>{s}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
            )}

            {(doc.integrity?.length ?? 0) > 0 && (
              <section>
                <div className="notice warn">
                  <strong>Dikkat:</strong> Bu CV, değerlendirme sistemine talimat
                  vermeye çalışan metin içeriyor. Puanlamaya <em>etki etmedi</em> —
                  kararı siz verin.
                </div>
                <div className="stack" style={{ gap: 6, marginTop: 8 }}>
                  {doc.integrity!.map((f, i) => (
                    <div key={i} className="req-card">
                      <span className="req-title">
                        {INJECTION_KIND_LABEL[f.kind] ?? f.kind}
                      </span>
                      <div className="evidence">&ldquo;{f.quote}&rdquo;</div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Kriter kriter değerlendirme */}
            <section>
              <span className="field-label">Kriterler</span>
              <div className="stack" style={{ gap: 8 }}>
                {doc.requirements.map((r) => (
                  <div key={r.req_id} className="req-card">
                    <div className="hstack" style={{ justifyContent: "space-between" }}>
                      <span className="req-title">
                        <span
                          className="mono"
                          style={{
                            color:
                              r.verdict === "met"
                                ? "var(--good)"
                                : r.verdict === "not_met" || r.verdict === "disqualified"
                                  ? "var(--bad)"
                                  : "var(--muted)",
                            marginRight: 8,
                          }}
                        >
                          {VERDICT_ICON[r.verdict]}
                        </span>
                        {r.label.tr ?? r.label.en ?? r.req_id}
                      </span>
                      <div className="hstack">
                        <span className="chip">{TYPE_LABEL[r.type]}</span>
                        {r.source_stage === "human" && (
                          <span className="chip accent" title="Bu kararı bir kişi düzeltti">
                            İK düzeltmesi
                          </span>
                        )}
                        {r.info_status && (
                          <span className={`chip${r.info_status === "explicit" ? " accent" : ""}`}>
                            {INFO_STATUS_LABEL[r.info_status]}
                          </span>
                        )}
                        {r.borderline && <span className="chip warn">Sınırda</span>}
                      </div>
                    </div>
                    <div className="tiny">
                      {VERDICT_LABEL[r.verdict]}
                      {r.level_label && r.verdict !== "unknown" ? ` · ${r.level_label}` : ""}
                      {r.score != null && r.verdict !== "unknown"
                        ? ` (${Math.round(r.score * 100)}/100)`
                        : ""}
                      {` · güven ${confidenceLabel(r.confidence)}`}
                    </div>
                    {r.reasoning && <div className="req-source">{r.reasoning}</div>}
                    {r.verdict === "unknown" && !r.reasoning && (
                      <div className="req-source">
                        CV&apos;de bu konuda bilgi bulunamadı — aday aleyhine sayılmadı.
                      </div>
                    )}
                    {r.evidence.map((e, i) =>
                      e.quote ? (
                        <div key={i} className="evidence">
                          &ldquo;{e.quote}&rdquo;{" "}
                          {e.page != null && <span className="page">s.{e.page}</span>}
                        </div>
                      ) : (
                        <div key={i} className="evidence rule">
                          {ruleEvidence(e)}
                        </div>
                      ),
                    )}

                    {editing === r.req_id ? (
                      <div className="correction">
                        <span className="tiny">Doğru değerlendirme sizce ne?</span>
                        <div className="hstack" style={{ gap: 6 }}>
                          {CORRECTABLE.filter((v) => v !== r.verdict).map((v) => (
                            <button
                              key={v}
                              className="btn seg-btn"
                              disabled={saving}
                              onClick={() => correct(r.req_id, v)}
                            >
                              {VERDICT_LABEL[v]}
                            </button>
                          ))}
                        </div>
                        <input
                          className="input"
                          placeholder="Kısa gerekçe (isteğe bağlı, kayda geçer)"
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                        />
                        <button
                          className="btn btn-ghost"
                          onClick={() => {
                            setEditing(null);
                            setReason("");
                          }}
                        >
                          Vazgeç
                        </button>
                      </div>
                    ) : (
                      <button
                        className="btn btn-ghost correction-open"
                        onClick={() => {
                          setEditing(r.req_id);
                          setReason("");
                        }}
                      >
                        Katılmıyorum — düzelt
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {(doc.missing_information.length > 0 ||
              (doc.narrative?.missing_information?.length ?? 0) > 0) && (
              <section>
                <span className="field-label">Eksik bilgiler (olumsuz sayılmaz)</span>
                <div className="hstack">
                  {[...doc.missing_information, ...(doc.narrative?.missing_information ?? [])].map(
                    (m, i) => (
                      <span key={i} className="chip outline">
                        {m}
                      </span>
                    ),
                  )}
                </div>
              </section>
            )}

            {doc.corrections.length > 0 && (
              <section>
                <span className="field-label">Derin analiz düzeltmeleri</span>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13.5 }}>
                  {doc.corrections.map((c, i) => (
                    <li key={i}>
                      {c.req_id}: {VERDICT_LABEL[c.from_verdict as keyof typeof VERDICT_LABEL] ?? c.from_verdict} →{" "}
                      {VERDICT_LABEL[c.to_verdict as keyof typeof VERDICT_LABEL] ?? c.to_verdict} — {c.note}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <p className="tiny" style={{ marginBottom: 0 }}>
              Değerlendirme sürümleri: profil v{detail.profile_version} · kriter v
              {detail.spec_version} · {detail.pipeline_version}. Yapay zekâ çıktıları kanıt
              alıntılarıyla doğrulanır; nihai karar her zaman sizindir.
            </p>
          </div>
        )}
      </aside>
    </>
  );
}
