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
} from "@/lib/format";
import type { ResultDetail } from "@/lib/types";

export default function CandidateDrawer({
  runId,
  applicationId,
  candidateName,
  onClose,
}: {
  runId: string;
  applicationId: string;
  candidateName: string | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<ResultDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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
                      {r.score != null && r.verdict !== "unknown"
                        ? ` · puan ${Math.round(r.score * 100)}/100`
                        : ""}
                      {` · güven ${confidenceLabel(r.confidence)}`}
                    </div>
                    {r.evidence.map((e, i) => (
                      <div key={i} className="evidence">
                        &ldquo;{e.quote}&rdquo;{" "}
                        {e.page != null && <span className="page">s.{e.page}</span>}
                      </div>
                    ))}
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
