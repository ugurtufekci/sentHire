"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { PARSE_ERROR_LABEL, PARSE_STATUS_LABEL, SENIORITY_LABEL, months } from "@/lib/format";
import type { CandidatesResponse } from "@/lib/types";

type LocalUpload = { name: string; state: "uploading" | "sent" | "error"; error?: string };

export default function UploadStep({
  jobId,
  onParsedCountChange,
}: {
  jobId: string;
  onParsedCountChange: (n: number) => void;
}) {
  const [data, setData] = useState<CandidatesResponse | null>(null);
  const [locals, setLocals] = useState<LocalUpload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quotaHit, setQuotaHit] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await api.jobCandidates(jobId);
      setData(response);
      onParsedCountChange(response.applications.length);
      const inFlight = response.files.some((f) =>
        ["pending", "parsing"].includes(f.parse_status),
      );
      if (!inFlight && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      if (inFlight && !pollRef.current) {
        pollRef.current = setInterval(refresh, 2500);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [jobId, onParsedCountChange]);

  useEffect(() => {
    refresh();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [refresh]);

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    setError(null);
    setQuotaHit(false);
    setLocals(files.map((f) => ({ name: f.name, state: "uploading" })));
    try {
      const { uploads } = await api.requestUploads(
        jobId,
        files.map((f) => ({ filename: f.name, content_type: f.type || "application/pdf" })),
      );
      const done: { s3_key: string; filename: string }[] = [];
      await Promise.all(
        uploads.map(async (slot, i) => {
          try {
            const put = await fetch(slot.url, {
              method: "PUT",
              headers: slot.headers,
              body: files[i],
            });
            if (!put.ok) throw new Error(`yükleme başarısız (${put.status})`);
            done.push({ s3_key: slot.s3_key, filename: slot.filename });
            setLocals((prev) =>
              prev.map((l) => (l.name === slot.filename ? { ...l, state: "sent" } : l)),
            );
          } catch (e) {
            setLocals((prev) =>
              prev.map((l) =>
                l.name === slot.filename
                  ? { ...l, state: "error", error: (e as Error).message }
                  : l,
              ),
            );
          }
        }),
      );
      if (done.length > 0) {
        await api.completeUploads(jobId, done);
        setLocals([]);
        await refresh();
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) {
        setQuotaHit(true);
        setLocals([]);
      } else {
        setError((e as Error).message);
      }
    }
  }

  const statusChip = (status: string) => {
    const label = PARSE_STATUS_LABEL[status] ?? status;
    const cls =
      status === "parsed" ? "chip good" : status === "failed" || status === "unsupported" ? "chip bad" : "chip";
    return <span className={cls}>{label}</span>;
  };

  return (
    <div className="stack">
      {error && <div className="notice bad">{error}</div>}
      {quotaHit && (
        <div className="notice warn">
          Bu ayki CV işleme kotanız bu yükleme için yetersiz.{" "}
          <Link href="/billing" style={{ color: "inherit", fontWeight: 600 }}>
            Plan ve kullanım
          </Link>{" "}
          sayfasından kalan hakkınızı görebilir veya planınızı yükseltebilirsiniz.
        </div>
      )}

      <div
        className={`dropzone${dragging ? " drag" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
      >
        <strong>CV&apos;leri buraya bırakın</strong>
        <span className="tiny">veya tıklayıp seçin · PDF · en fazla 500 dosya</span>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          hidden
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {(locals.length > 0 || (data && data.files.length > 0)) && (
        <div className="card quiet">
          {locals.map((l) => (
            <div key={l.name} className="file-row">
              <span className="file-name">{l.name}</span>
              {l.state === "uploading" && (
                <span className="chip">
                  <span className="pulse" /> Yükleniyor
                </span>
              )}
              {l.state === "sent" && <span className="chip">Sıraya alındı</span>}
              {l.state === "error" && <span className="chip bad">{l.error ?? "Hata"}</span>}
            </div>
          ))}
          {data?.files.map((f) => (
            <div key={f.document_id} className="file-row">
              <span className="file-name">{f.filename ?? f.document_id}</span>
              {f.document_kind !== "cv" && f.parse_status === "parsed" && (
                <span className="chip warn">CV değil ({f.document_kind})</span>
              )}
              {f.error && <span className="tiny">{PARSE_ERROR_LABEL[f.error] ?? f.error}</span>}
              {statusChip(f.parse_status)}
            </div>
          ))}
        </div>
      )}

      {data && data.applications.length > 0 && (
        <div className="card quiet">
          <span className="field-label">
            Profili çıkarılan adaylar ({data.applications.length})
          </span>
          <div className="table-scroll">
            <table className="soft-table">
              <thead>
                <tr>
                  <th>Aday</th>
                  <th>Deneyim</th>
                  <th>Şehir</th>
                  <th>Kıdem</th>
                </tr>
              </thead>
              <tbody>
                {data.applications.map((a) => (
                  <tr key={a.application_id}>
                    <td>{a.candidate.display_name ?? "İsimsiz aday"}</td>
                    <td>{months(a.profile_summary?.total_experience_months)}</td>
                    <td>{a.profile_summary?.city ?? "—"}</td>
                    <td>{SENIORITY_LABEL[a.profile_summary?.seniority ?? "unknown"] ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
