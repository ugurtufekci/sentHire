"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Job } from "@/lib/types";

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listJobs()
      .then(setJobs)
      .catch((e: ApiError) => setError(e.message));
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
