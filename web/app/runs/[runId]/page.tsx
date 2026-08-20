"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import CandidateDrawer from "@/components/CandidateDrawer";
import { api } from "@/lib/api";
import {
  BAND_LABEL,
  RUN_STATUS_LABEL,
  STAGE_LABEL,
  bandClass,
  confidenceLabel,
  usd,
} from "@/lib/format";
import type { ResultRow, ResultsResponse, RunStatus } from "@/lib/types";

const ACTIVE_PHASES = ["queued", "screening", "selecting", "deep_analysis", "scoring"];

const CRITERION_FLAG_LABEL: Record<string, string> = {
  no_discrimination:
    "hiçbir adayı ayırt etmedi — herkes aynı seviyede çıktı, kriter fazla genel olabilir",
  mostly_unknown: "adayların çoğunda CV'de bilgi yoktu — puanlamaya girmedi",
  all_unknown: "hiçbir adayda bilgi bulunamadı",
};

/** Which criteria did any work. A requirement carrying weight while landing
 *  every candidate on the same rung is spending budget on nothing. */
function CriteriaNotes({
  rows,
}: {
  rows: NonNullable<import("@/lib/types").RunStatus["funnel"]["consistency"]>;
}) {
  const flagged = rows.filter((r) => r.flag);
  if (flagged.length === 0) return null;
  return (
    <div className="card quiet" style={{ marginBottom: 16 }}>
      <span className="field-label">Kriterler hakkında</span>
      <ul className="stack" style={{ gap: 4, margin: 0, paddingLeft: 18 }}>
        {flagged.map((row) => (
          <li key={row.req_id} className="tiny">
            <strong>{row.label}</strong> — {CRITERION_FLAG_LABEL[row.flag!] ?? row.flag}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function RunPage() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId;

  const [run, setRun] = useState<RunStatus | null>(null);
  const [results, setResults] = useState<ResultsResponse | null>(null);
  const [open, setOpen] = useState<ResultRow | null>(null);
  const [showRejected, setShowRejected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const status = await api.runStatus(runId);
      setRun(status);
      if (!ACTIVE_PHASES.includes(status.status)) {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        if (status.status === "complete") {
          setResults(await api.runResults(runId));
        }
      }
    } catch (e) {
      setError((e as Error).message);
      if (pollRef.current) clearInterval(pollRef.current);
    }
  }, [runId]);

  useEffect(() => {
    refresh();
    pollRef.current = setInterval(refresh, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [refresh]);

  const funnel = run?.funnel;
  const batchPending = Object.values(funnel?.batch ?? {}).reduce(
    (sum, entry) => sum + (entry?.submitted ?? 0),
    0,
  );
  const totalUsd = Object.values(run?.cost ?? {}).reduce((sum, c) => sum + (c.usd ?? 0), 0);
  const savedUsd = Object.values(run?.cost ?? {}).reduce((sum, c) => sum + (c.usd_saved ?? 0), 0);
  const total = funnel?.total ?? 0;
  const evaluated = funnel?.evaluated_so_far ?? 0;
  const deepCount =
    funnel?.deep_analyzed ?? funnel?.by_stage?.deep ?? funnel?.deep_pending?.length ?? 0;
  const active = run ? ACTIVE_PHASES.includes(run.status) : true;

  const bar = (label: string, value: number, max: number) => (
    <div className="funnel-row">
      <span className="funnel-label">{label}</span>
      <span className="funnel-track">
        <span
          className="funnel-fill"
          style={{ width: max > 0 ? `${Math.min(100, (value / max) * 100)}%` : "0%" }}
        />
      </span>
      <span className="funnel-count mono">{value}</span>
    </div>
  );

  return (
    <main>
      <div className="hstack" style={{ justifyContent: "space-between" }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          Tarama
        </h1>
        {run && (
          <Link href={`/jobs/${run.job_id}`} className="btn btn-ghost">
            İlana dön
          </Link>
        )}
      </div>
      <p className="page-sub">
        {run ? RUN_STATUS_LABEL[run.status] ?? run.status : "Yükleniyor…"}
        {active && (
          <>
            {" "}
            <span className="pulse" />
          </>
        )}
      </p>

      {error && <div className="notice bad">{error}</div>}

      {run && (
        <div className="card" style={{ marginBottom: 28 }}>
          {bar("Değerlendirilen", evaluated, Math.max(total, 1))}
          {bar("Ön filtreyi geçen", (funnel?.evaluated ?? evaluated) - (funnel?.hard_failed ?? 0), Math.max(total, 1))}
          {bar("Derin analiz", deepCount, Math.max(total, 1))}
          {funnel?.ranked != null && bar("Sıralanan", funnel.ranked, Math.max(total, 1))}
          {(funnel?.memoized ?? 0) > 0 && (
            <div className="tiny" style={{ marginTop: 8 }}>
              {funnel!.memoized} aday önceki taramadan hazır geldi — yeniden işlenmedi.
            </div>
          )}
          {run.mode === "batch" && active && (
            <div className="notice accent" style={{ marginTop: 12 }}>
              <strong>Ekonomik mod</strong> — {batchPending} aday toplu işlemde. Yapay zekâ
              maliyeti yarı yarıya düşer; sonuçlar genellikle bir saat içinde hazır olur (en geç
              24 saat). Bu sayfayı kapatabilirsiniz, tarama arka planda sürer.
            </div>
          )}
          {funnel?.error && (
            <div className="notice bad" style={{ marginTop: 12 }}>
              {funnel.error}
            </div>
          )}
          {run.status === "complete" && Object.keys(run.cost).length > 0 && (
            <div className="tiny" style={{ marginTop: 8 }}>
              Model kullanımı:{" "}
              {Object.entries(run.cost)
                .map(
                  ([stage, c]) =>
                    `${STAGE_LABEL[stage] ?? stage} ${c.calls} çağrı`,
                )
                .join(" · ")}
              {totalUsd > 0 && ` · yaklaşık ${usd(totalUsd)}`}
              {savedUsd > 0 && (
                <span className="saved"> · ekonomik mod {usd(savedUsd)} tasarruf</span>
              )}
            </div>
          )}
        </div>
      )}

      {run?.status === "complete" && (run.funnel.consistency?.length ?? 0) > 0 && (
        <CriteriaNotes rows={run.funnel.consistency!} />
      )}

      {results && run?.status === "complete" && results.results.length > 0 && (
        <div className="notice accent" style={{ marginBottom: 16 }}>
          Beğendiğiniz adayları görüşme sürecine taşıyın —{" "}
          <Link href={`/jobs/${run.job_id}/pipeline`}>aday akışını açın</Link>.
        </div>
      )}

      {results && (
        <>
          <h2 style={{ fontSize: 17, margin: "0 0 12px" }}>
            Sıralama{" "}
            <span className="tiny" style={{ fontWeight: 400 }}>
              — satıra tıklayınca kanıtlarıyla tam döküm açılır
            </span>
          </h2>
          <div className="card" style={{ padding: "6px 10px" }}>
            <div className="table-scroll">
              <table className="soft-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>#</th>
                    <th>Aday</th>
                    <th style={{ width: 70 }}>Puan</th>
                    <th style={{ width: 90 }}>Seviye</th>
                    <th style={{ width: 80 }}>Güven</th>
                    <th>Öne çıkanlar</th>
                  </tr>
                </thead>
                <tbody>
                  {results.results.length === 0 && (
                    <tr>
                      <td colSpan={6}>
                        <div className="empty">Ön filtreyi geçen aday olmadı.</div>
                      </td>
                    </tr>
                  )}
                  {results.results.map((r, index) => {
                    const tiedAbove =
                      index > 0 &&
                      results.results[index - 1].equivalent_group === r.equivalent_group;
                    const tiedBelow =
                      index < results.results.length - 1 &&
                      results.results[index + 1].equivalent_group === r.equivalent_group;
                    return (
                    <tr key={r.application_id} className="row-hover" onClick={() => setOpen(r)}>
                      <td className="mono">
                        {r.rank}
                        {(tiedAbove || tiedBelow) && (
                          <span
                            className="tie-mark"
                            title="Bu adaylar birbirinden ayırt edilecek kadar farklı puan almadı"
                          >
                            ≈
                          </span>
                        )}
                      </td>
                      <td>
                        {r.candidate.display_name ?? "İsimsiz aday"}
                        {r.needs_review && (
                          <span className="chip warn" style={{ marginLeft: 8 }}>
                            İnceleme önerilir
                          </span>
                        )}
                      </td>
                      <td className="mono">
                        {r.overall_score != null ? Math.round(r.overall_score) : "—"}
                      </td>
                      <td>
                        <span className={bandClass(r.band)}>{BAND_LABEL[r.band ?? ""] ?? r.band}</span>
                      </td>
                      <td className="tiny">{confidenceLabel(r.confidence)}</td>
                      <td className="tiny">
                        {r.headline.strengths.slice(0, 2).join(" · ") || "—"}
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {(results.rejected?.length ?? 0) > 0 && (
            <div style={{ marginTop: 20 }}>
              <button className="btn btn-ghost" onClick={() => setShowRejected((v) => !v)}>
                {showRejected ? "Elenenleri gizle" : `Elenenler (${results.rejected!.length})`}
              </button>
              {showRejected && (
                <div className="card" style={{ marginTop: 10, padding: "6px 10px" }}>
                  <div className="table-scroll">
                    <table className="soft-table">
                      <thead>
                        <tr>
                          <th>Aday</th>
                          <th>Neden</th>
                        </tr>
                      </thead>
                      <tbody>
                        {results.rejected!.map((r) => (
                          <tr key={r.application_id} className="row-hover" onClick={() => setOpen(r)}>
                            <td>{r.candidate.display_name ?? "İsimsiz aday"}</td>
                            <td className="tiny">
                              {(r.rejection_reasons ?? []).map((x) => x.label).join(" · ") || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="tiny" style={{ margin: "8px 4px" }}>
                    Elenen her aday, hangi zorunlu kritere takıldıysa onunla birlikte listelenir —
                    karar her zaman gözden geçirilebilir.
                  </p>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {open && (
        <CandidateDrawer
          runId={runId}
          applicationId={open.application_id}
          candidateName={open.candidate.display_name}
          onClose={() => setOpen(null)}
          onCorrected={() => api.runResults(runId).then(setResults).catch(() => {})}
        />
      )}
    </main>
  );
}
