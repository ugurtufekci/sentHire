"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  CATEGORY_LABEL,
  EVALUATOR_LABEL,
  IMPORTANCE_LABEL,
  SPEC_STATUS_LABEL,
  TYPE_LABEL,
  stripUnderstoodPrefix,
} from "@/lib/format";
import type { Requirement, RequirementType, SpecDocument, SpecRow } from "@/lib/types";

const EDITABLE_TYPES: RequirementType[] = ["hard", "scored", "bonus", "penalty", "info"];

export default function RequirementsStep({
  jobId,
  onConfirmedChange,
}: {
  jobId: string;
  onConfirmedChange: (confirmed: boolean) => void;
}) {
  const [specs, setSpecs] = useState<SpecRow[]>([]);
  const [active, setActive] = useState<SpecRow | null>(null); // full row incl. spec doc
  const [draftDoc, setDraftDoc] = useState<SpecDocument | null>(null);
  const [nlText, setNlText] = useState("");
  const [showEditor, setShowEditor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };

  const loadActive = useCallback(
    async (rows: SpecRow[]) => {
      const interesting =
        rows.find((r) => r.status === "compiling") ??
        rows.find((r) => r.status === "draft") ??
        rows.find((r) => r.status === "confirmed") ??
        null;
      if (!interesting) {
        setActive(null);
        onConfirmedChange(false);
        return;
      }
      const full = await api.getSpec(interesting.spec_id);
      setActive(full);
      onConfirmedChange(full.status === "confirmed");
      if (full.status === "draft" && full.spec) setDraftDoc(full.spec);
      if (full.status === "compiling" && !pollRef.current) {
        pollRef.current = setInterval(async () => {
          const refreshed = await api.getSpec(interesting.spec_id);
          if (refreshed.status !== "compiling") {
            stopPoll();
            setActive(refreshed);
            if (refreshed.status === "draft" && refreshed.spec) setDraftDoc(refreshed.spec);
          }
        }, 2000);
      }
    },
    [onConfirmedChange],
  );

  useEffect(() => {
    api
      .listSpecs(jobId)
      .then((rows) => {
        setSpecs(rows);
        return loadActive(rows);
      })
      .catch((e) => setError((e as Error).message));
    return stopPoll;
  }, [jobId, loadActive]);

  async function compile() {
    if (!nlText.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const row = await api.compileRequirements(jobId, nlText.trim());
      const rows = await api.listSpecs(jobId);
      setSpecs(rows);
      setShowEditor(false);
      await loadActive([row, ...rows]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      const confirmed = await api.confirmSpec(active.spec_id, draftDoc);
      setActive(confirmed);
      setDraftDoc(null);
      onConfirmedChange(true);
      setSpecs(await api.listSpecs(jobId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function updateRequirement(reqId: string, patch: Partial<Requirement>) {
    if (!draftDoc) return;
    setDraftDoc({
      ...draftDoc,
      requirements: draftDoc.requirements.map((r) =>
        r.req_id === reqId ? { ...r, ...patch } : r,
      ),
    });
  }

  const compilerMeta = active?.spec?.compiler;

  return (
    <div className="stack">
      {error && <div className="notice bad">{error}</div>}

      {/* State: nothing yet, or user opening a new version */}
      {(!active || showEditor) && (
        <div className="card">
          <label className="field-label" htmlFor="nl-text">
            Aradığınız adayı kendi cümlelerinizle anlatın (Türkçe veya İngilizce)
          </label>
          <textarea
            id="nl-text"
            className="textarea"
            placeholder={
              "ör. Ankara'da ikamet etmesi önemli. Çok sık iş değiştirmiş olmasın. " +
              "En az 3 yıl B2B satış deneyimi olsun. SaaS deneyimi varsa avantaj."
            }
            value={nlText}
            onChange={(e) => setNlText(e.target.value)}
          />
          <div className="hstack" style={{ marginTop: 12 }}>
            <button className="btn btn-primary" onClick={compile} disabled={busy || !nlText.trim()}>
              {busy ? "Gönderiliyor…" : "Analiz et"}
            </button>
            {showEditor && (
              <button className="btn btn-ghost" onClick={() => setShowEditor(false)}>
                Vazgeç
              </button>
            )}
            <span className="tiny">
              Cümleleriniz yapılandırılmış kriterlere çevrilir; onaylamadan hiçbir şey çalışmaz.
            </span>
          </div>
        </div>
      )}

      {/* State: compiling */}
      {active?.status === "compiling" && (
        <div className="card hstack">
          <span className="pulse" />
          <span>Cümleleriniz kriterlere çevriliyor — birkaç saniye sürer…</span>
        </div>
      )}

      {/* State: failed */}
      {active?.status === "failed" && (
        <div className="notice bad">
          Derleme başarısız oldu{active.spec?.error ? `: ${active.spec.error}` : "."} Metni
          düzenleyip yeniden deneyin.
        </div>
      )}

      {/* State: draft → review & confirm */}
      {active?.status === "draft" && draftDoc && (
        <div className="stack">
          {compilerMeta?.back_translation?.tr && (
            <div className="notice accent">
              <strong>Anladığımız:</strong> {stripUnderstoodPrefix(compilerMeta.back_translation.tr)}
            </div>
          )}

          {(compilerMeta?.compliance_flags ?? []).map((flag, i) => (
            <div key={i} className={`notice ${flag.action === "blocked" ? "bad" : "warn"}`}>
              {flag.action === "blocked" ? "Kullanılamayan kriter: " : "Yeniden yazıldı: "}
              &ldquo;{flag.original_text}&rdquo; — {flag.issue}
              {flag.rewritten_to ? ` → ${flag.rewritten_to}` : ""}
            </div>
          ))}

          <div className="stack">
            {draftDoc.requirements.map((req) => (
              <div key={req.req_id} className="req-card">
                <div className="hstack" style={{ justifyContent: "space-between" }}>
                  <span className="req-title">{req.label.tr ?? req.label.en ?? req.req_id}</span>
                  <div className="hstack">
                    <span className="chip">{CATEGORY_LABEL[req.category] ?? req.category}</span>
                    <span className="chip">{EVALUATOR_LABEL[req.evaluator]}</span>
                    <span className="chip outline">{IMPORTANCE_LABEL[req.importance]}</span>
                    <select
                      className="select"
                      value={req.type}
                      onChange={(e) =>
                        updateRequirement(req.req_id, {
                          type: e.target.value as RequirementType,
                        })
                      }
                      aria-label="Kriter türü"
                    >
                      {(EDITABLE_TYPES.includes(req.type)
                        ? EDITABLE_TYPES
                        : [...EDITABLE_TYPES, req.type]
                      ).map((t) => (
                        <option key={t} value={t}>
                          {TYPE_LABEL[t]}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {req.source?.original && (
                  <div className="req-source">&ldquo;{req.source.original}&rdquo;</div>
                )}
                {req.clarification?.question && (
                  <div className="req-question">
                    {req.clarification.question}
                    {req.clarification.default ? ` (varsayılan: ${req.clarification.default})` : ""}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="card quiet">
            <span className="field-label">Kategori ağırlıkları (%)</span>
            <div className="hstack">
              {Object.entries(draftDoc.weights).map(([cat, w]) => (
                <label key={cat} className="chip" style={{ gap: 8 }}>
                  {CATEGORY_LABEL[cat] ?? cat}
                  <input
                    className="mono"
                    style={{ width: 44, border: "none", background: "transparent", color: "inherit", font: "inherit", textAlign: "right" }}
                    value={Math.round(w * 100)}
                    onChange={(e) => {
                      const parsed = Number(e.target.value);
                      if (Number.isNaN(parsed)) return;
                      setDraftDoc({
                        ...draftDoc,
                        weights: { ...draftDoc.weights, [cat]: Math.max(0, parsed) / 100 },
                      });
                    }}
                    inputMode="numeric"
                    aria-label={`${CATEGORY_LABEL[cat] ?? cat} ağırlığı`}
                  />
                </label>
              ))}
            </div>
            <div className="tiny" style={{ marginTop: 8 }}>
              Ağırlıklar onay sırasında otomatik normalize edilir. Puanlama tamamen
              deterministiktir — ağırlık değişikliği sonrası yeniden sıralama ücretsizdir.
            </div>
          </div>

          <div className="hstack">
            <button className="btn btn-primary" onClick={confirm} disabled={busy}>
              {busy ? "Onaylanıyor…" : "Kriterleri onayla"}
            </button>
            <span className="tiny">Onay sonrası bu sürüm sabitlenir (v{active.version}).</span>
          </div>
        </div>
      )}

      {/* State: confirmed */}
      {active?.status === "confirmed" && !showEditor && (
        <div className="card">
          <div className="hstack" style={{ justifyContent: "space-between" }}>
            <div className="hstack">
              <span className="chip good">Onaylı · v{active.version}</span>
              <span className="tiny">{active.spec?.requirements.length ?? 0} kriter</span>
            </div>
            <button
              className="btn btn-ghost"
              onClick={() => {
                setNlText("");
                setShowEditor(true);
              }}
            >
              Kriterleri güncelle
            </button>
          </div>
          <div className="hstack" style={{ marginTop: 10 }}>
            {active.spec?.requirements.map((r) => (
              <span key={r.req_id} className="chip" title={TYPE_LABEL[r.type]}>
                {r.label.tr ?? r.label.en ?? r.req_id}
                {r.type === "hard" ? " · zorunlu" : r.type === "bonus" ? " · avantaj" : ""}
              </span>
            ))}
          </div>
          {compilerMeta?.back_translation?.tr && (
            <p className="tiny" style={{ marginTop: 10, marginBottom: 0 }}>
              {stripUnderstoodPrefix(compilerMeta.back_translation.tr)}
            </p>
          )}
        </div>
      )}

      {specs.filter((s) => s.status === "superseded").length > 0 && (
        <div className="tiny">
          Önceki sürümler:{" "}
          {specs
            .filter((s) => s.status === "superseded")
            .map((s) => `v${s.version}`)
            .join(", ")}{" "}
          ({SPEC_STATUS_LABEL.superseded.toLowerCase()})
        </div>
      )}
    </div>
  );
}
