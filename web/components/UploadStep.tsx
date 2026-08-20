"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

// Chromium allows six connections per host; anything past that queues, and a
// large enough queue gets aborted outright.
const UPLOAD_CONCURRENCY = 4;
const UPLOAD_ATTEMPTS = 2;

async function inBatches<T>(
  items: T[],
  size: number,
  worker: (item: T, index: number) => Promise<void>,
): Promise<void> {
  for (let start = 0; start < items.length; start += size) {
    await Promise.all(
      items.slice(start, start + size).map((item, offset) => worker(item, start + offset)),
    );
  }
}

async function putWithRetry(
  slot: { url: string; headers: Record<string, string>; filename: string },
  file: File,
): Promise<void> {
  let lastError: Error | null = null;
  for (let attempt = 1; attempt <= UPLOAD_ATTEMPTS; attempt++) {
    try {
      const response = await fetch(slot.url, {
        method: "PUT",
        headers: slot.headers,
        body: file,
      });
      if (!response.ok) throw new Error(`yükleme başarısız (${response.status})`);
      return;
    } catch (e) {
      lastError = e as Error;
      if (attempt < UPLOAD_ATTEMPTS) {
        await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
      }
    }
  }
  throw lastError ?? new Error("yükleme başarısız");
}
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
      return response;
    } catch (e) {
      setError((e as Error).message);
      return null;
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
      const failed: string[] = [];

      // Uploads run a few at a time, not all at once. A folder of 500 CVs is
      // the advertised case, and 500 simultaneous requests exhaust the
      // browser's connection pool — the excess is aborted, which looked to the
      // user like files silently vanishing.
      await inBatches(uploads, UPLOAD_CONCURRENCY, async (slot, i) => {
        try {
          await putWithRetry(slot, files[i]);
          done.push({ s3_key: slot.s3_key, filename: slot.filename });
          setLocals((prev) =>
            prev.map((l) => (l.name === slot.filename ? { ...l, state: "sent" } : l)),
          );
        } catch (e) {
          failed.push(slot.filename);
          setLocals((prev) =>
            prev.map((l) =>
              l.name === slot.filename
                ? { ...l, state: "error", error: (e as Error).message }
                : l,
            ),
          );
        }
      });

      if (failed.length > 0) {
        setError(
          `${failed.length} dosya yüklenemedi (${failed.slice(0, 3).join(", ")}` +
            `${failed.length > 3 ? "…" : ""}). Tekrar deneyebilirsiniz — ` +
            "yüklenenler korunur.",
        );
      }
      if (done.length > 0) {
        await api.completeUploads(jobId, done);
        // Keep the local rows until the server has caught up, so the list never
        // goes blank, and watch for the documents the workers are creating.
        await waitForDocuments(done.length);
        setLocals([]);
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

  /** Poll until the freshly uploaded documents appear, then hand over to the
   *  in-flight poller. Without this the list can be fetched in the gap between
   *  "queued" and "row exists" and, finding nothing in flight, never look
   *  again — leaving the user staring at an empty list. */
  async function waitForDocuments(expected: number) {
    const deadline = Date.now() + 120000;
    let seen = 0;
    while (Date.now() < deadline) {
      const response = await refresh();
      seen = response?.files.length ?? 0;
      if (seen >= expected) return;
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    setError(
      `${expected} dosya yüklendi ama ${seen} tanesi göründü. ` +
        "Sayfayı yenileyip tekrar bakın — dosyalar kaybolmadı, işlenmeleri gecikmiş olabilir.",
    );
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
