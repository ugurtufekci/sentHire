"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatDate, PIPELINE_STAGE_LABEL } from "@/lib/format";
import type { AgendaItem, Job } from "@/lib/types";

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [agenda, setAgenda] = useState<AgendaItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listJobs()
      .then(setJobs)
      .catch((e: ApiError) => setError(e.message));
    // The reminder list is decoration here — its failure must not block the page.
    api
      .agenda()
      .then((a) => setAgenda(a.items))
      .catch(() => setAgenda([]));
  }, []);

  return (
    <main>
      <div className="hstack" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          İlanlar
        </h1>
        <Link href="/jobs/new" className="btn btn-primary">
          Yeni ilan
        </Link>
      </div>
      <p className="page-sub">
        Bir ilan oluşturun, aradığınız adayı kendi cümlelerinizle anlatın, CV&apos;leri yükleyin —
        gerisini sentHire halletsin.
      </p>

      {error && <div className="notice bad">Bağlantı hatası: {error}</div>}

      {agenda.length > 0 && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="hstack" style={{ justifyContent: "space-between", marginBottom: 6 }}>
            <strong>Yaklaşan adımlar</strong>
            <span className="tiny">{agenda.length} hatırlatıcı</span>
          </div>
          {agenda.slice(0, 6).map((item) => (
            <Link
              key={item.application_id}
              href={`/jobs/${item.job_id}/pipeline`}
              className="agenda-row"
            >
              <span className={`agenda-when ${item.overdue ? "late" : ""}`}>
                {item.overdue ? "Gecikti" : formatDate(item.next_action_at)}
              </span>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {item.candidate_name ?? "İsimsiz aday"}
                {item.next_action && <span className="tiny"> — {item.next_action}</span>}
              </span>
              <span className="chip">{PIPELINE_STAGE_LABEL[item.stage]}</span>
              <span className="tiny">{item.job_title}</span>
            </Link>
          ))}
        </div>
      )}

      {jobs === null && !error && <div className="empty">Yükleniyor…</div>}

      {jobs?.length === 0 && (
        <div className="card">
          <div className="empty">
            Henüz ilan yok. İlk ilanınızı oluşturun — ilk sıralamayı birkaç dakika içinde
            görürsünüz.
          </div>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="stack">
          {jobs.map((job) => (
            <Link key={job.id} href={`/jobs/${job.id}`} className="card row-hover" style={{ display: "block", color: "inherit" }}>
              <div className="hstack" style={{ justifyContent: "space-between" }}>
                <strong>{job.title}</strong>
                <span className="tiny">{formatDate(job.created_at)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
