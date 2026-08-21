"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import RequirementsStep from "@/components/RequirementsStep";
import UploadStep from "@/components/UploadStep";
import { api } from "@/lib/api";
import { RUN_STATUS_LABEL, formatDate } from "@/lib/format";
import type { Job, RunPhase } from "@/lib/types";

interface RunSummary {
  run_id: string;
  status: RunPhase;
  started_at: string | null;
  finished_at: string | null;
  funnel: { ranked?: number; total?: number };
}

export default function JobPage() {
  const params = useParams<{ jobId: string }>();
  const router = useRouter();
  const jobId = params.jobId;

  const [job, setJob] = useState<Job | null>(null);
  const [specConfirmed, setSpecConfirmed] = useState(false);
  const [parsedCount, setParsedCount] = useState(0);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"interactive" | "batch">("interactive");

  useEffect(() => {
    api.getJob(jobId).then(setJob).catch((e) => setError((e as Error).message));
    api.listRuns(jobId).then(setRuns).catch(() => setRuns([]));
  }, [jobId]);

  async function startRun() {
    setStarting(true);
    setError(null);
    try {
      const { run_id } = await api.startRun(jobId, mode);
      router.push(`/runs/${run_id}`);
    } catch (e) {
      setError((e as Error).message);
      setStarting(false);
    }
  }

  const canRun = specConfirmed && parsedCount > 0;

  return (
    <main>
      <div className="hstack" style={{ justifyContent: "space-between" }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          {job?.title ?? "…"}
        </h1>
        <div className="hstack">
          {job?.status === "closed" && <span className="chip">Kapalı</span>}
          {job && (
            <button
              className="btn"
              onClick={() =>
                api
                  .updateJob(jobId, {
                    status: job.status === "closed" ? "active" : "closed",
                  })
                  .then(setJob)
                  .catch((e) => setError((e as Error).message))
              }
            >
              {job.status === "closed" ? "İlanı yeniden aç" : "İlanı kapat"}
            </button>
          )}
          <Link href={`/jobs/${jobId}/pipeline`} className="btn">
            Aday akışı →
          </Link>
        </div>
      </div>
      <p className="page-sub">
        Üç adım: kriterlerinizi anlatın ve onaylayın, CV&apos;leri yükleyin, taramayı başlatın.
        Kriterleri sonradan değiştirirseniz CV&apos;ler yeniden işlenmez — sadece etkilenen
        kısımlar güncellenir.
      </p>

      {error && (
        <div className="notice bad" style={{ marginBottom: 20 }}>
          {error}
        </div>
      )}

      <div className="journey">
        <section className="step">
          <div className={`step-marker${specConfirmed ? " done" : ""}`}>
            {specConfirmed ? "✓" : "1"}
          </div>
          <div>
            <h2>Aradığınız adayı anlatın</h2>
            <p className="step-hint">
              Şablon kriterleri ve sizin cümleleriniz birlikte, onayınızdan geçen tek bir
              değerlendirme setine dönüşür.
            </p>
            <RequirementsStep jobId={jobId} onConfirmedChange={setSpecConfirmed} />
          </div>
        </section>

        <section className="step">
          <div className={`step-marker${parsedCount > 0 ? " done" : ""}`}>
            {parsedCount > 0 ? "✓" : "2"}
          </div>
          <div>
            <h2>CV&apos;leri yükleyin</h2>
            <p className="step-hint">
              Aynı CV daha önce yüklendiyse yeniden işlenmez; sorunlu dosyalar diğerlerini
              engellemez.
            </p>
            <UploadStep jobId={jobId} onParsedCountChange={setParsedCount} />
          </div>
        </section>

        <section className="step">
          <div className="step-marker">3</div>
          <div>
            <h2>Taramayı başlatın</h2>
            <p className="step-hint">
              Adaylar önce kurallarla, sonra yapay zekâ ile değerlendirilir; sonuçların her
              satırı kanıtıyla birlikte gelir.
            </p>
            <div className="stack">
              <div className="mode-choice">
                {(
                  [
                    {
                      id: "interactive" as const,
                      title: "Hemen sonuç",
                      hint: "Sonuçlar dakikalar içinde gelir. Az sayıda CV ve acil ilanlar için.",
                    },
                    {
                      id: "batch" as const,
                      title: "Ekonomik mod",
                      hint: "Yapay zekâ maliyeti yarı yarıya düşer; sonuçlar genellikle bir saat içinde (en geç 24 saatte) hazır olur. Büyük havuzlar için.",
                    },
                  ] as const
                ).map((option) => (
                  <label
                    key={option.id}
                    className={`mode-option${mode === option.id ? " selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="run-mode"
                      value={option.id}
                      checked={mode === option.id}
                      onChange={() => setMode(option.id)}
                    />
                    <span>
                      <strong>{option.title}</strong>
                      <span className="tiny">{option.hint}</span>
                    </span>
                  </label>
                ))}
              </div>

              <div className="hstack">
                <button
                  className="btn btn-primary"
                  onClick={startRun}
                  disabled={!canRun || starting}
                >
                  {starting ? "Başlatılıyor…" : "Taramayı başlat"}
                </button>
                {!canRun && (
                  <span className="tiny">
                    {!specConfirmed
                      ? "Önce kriterleri onaylayın."
                      : "En az bir CV işlenmiş olmalı."}
                  </span>
                )}
              </div>

              {runs.length > 0 && (
                <div className="card quiet">
                  <span className="field-label">Önceki taramalar</span>
                  <div className="stack" style={{ gap: 6 }}>
                    {runs.map((r) => (
                      <Link
                        key={r.run_id}
                        href={`/runs/${r.run_id}`}
                        className="hstack"
                        style={{ justifyContent: "space-between", color: "inherit" }}
                      >
                        <span className="tiny">{formatDate(r.started_at)}</span>
                        <span className="chip">
                          {RUN_STATUS_LABEL[r.status] ?? r.status}
                          {r.status === "complete" && r.funnel?.ranked != null
                            ? ` · ${r.funnel.ranked} aday sıralandı`
                            : ""}
                        </span>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
