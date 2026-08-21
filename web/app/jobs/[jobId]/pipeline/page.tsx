"use client";

/** Hiring pipeline board: pick the good scores, drag them through the process.
 *
 * One screen, three zones: a tray of screened-but-untouched candidates with
 * quick-select shortcuts, a kanban board moved by native HTML5 drag & drop
 * (and by a select in the drawer, for keyboards and phones), and a drawer with
 * the candidate's full timeline and quick forms for notes/meetings/contacts.
 * Every mutation is optimistic; a failure reloads the truth from the server.
 */

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import MessageComposer from "@/components/MessageComposer";
import { api, ApiError } from "@/lib/api";
import {
  bandClass,
  BAND_LABEL,
  EVENT_KIND_LABEL,
  formatDate,
  PIPELINE_STAGE_LABEL,
  scoreText,
} from "@/lib/format";
import type {
  JobInsights,
  SentMessage,
  PipelineBoard,
  PipelineCard,
  PipelineStage,
  TimelineResponse,
} from "@/lib/types";

const NO_NAME = "İsimsiz aday";

function isOverdue(iso: string | null): boolean {
  return iso != null && new Date(iso).getTime() < Date.now();
}

export default function PipelinePage({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = use(params);
  const [board, setBoard] = useState<PipelineBoard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dragging, setDragging] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [drawerId, setDrawerId] = useState<string | null>(null);
  const [moving, setMoving] = useState(false);
  const [insights, setInsights] = useState<JobInsights | null>(null);
  const [composing, setComposing] = useState<{ ids: string[]; title: string } | null>(null);

  const reload = useCallback(() => {
    api
      .pipelineBoard(jobId)
      .then((b) => {
        setBoard(b);
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
    // Learned-from-outcomes panel: informative, never load-bearing, so a
    // failure here must not take the board down with it.
    api
      .jobInsights(jobId)
      .then(setInsights)
      .catch(() => setInsights(null));
  }, [jobId]);

  useEffect(reload, [reload]);

  /** Optimistically move one card to a stage, then confirm with the server. */
  const moveCard = useCallback(
    (applicationId: string, stage: PipelineStage) => {
      setBoard((prev) => {
        if (!prev) return prev;
        let card: PipelineCard | undefined;
        const tray = prev.tray.filter((c) => {
          if (c.application_id === applicationId) {
            card = c;
            return false;
          }
          return true;
        });
        const columns = Object.fromEntries(
          Object.entries(prev.columns).map(([s, cards]) => [
            s,
            cards.filter((c) => {
              if (c.application_id === applicationId) {
                card = c;
                return false;
              }
              return true;
            }),
          ]),
        );
        if (!card || card.stage === stage) return prev;
        const movedCard = { ...card, stage, stage_changed_at: new Date().toISOString() };
        columns[stage] = [...(columns[stage] ?? []), movedCard];
        return { ...prev, tray, columns };
      });
      setSelected((prev) => {
        if (!prev.has(applicationId)) return prev;
        const next = new Set(prev);
        next.delete(applicationId);
        return next;
      });
      api.moveStage(applicationId, stage).catch((e: ApiError) => {
        setError(e.message);
        reload();
      });
    },
    [reload],
  );

  const shortlistSelected = useCallback(() => {
    if (selected.size === 0 || !board) return;
    setMoving(true);
    api
      .shortlist(jobId, [...selected])
      .then(() => {
        setSelected(new Set());
        reload();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setMoving(false));
  }, [board, jobId, reload, selected]);

  const tray = board?.tray ?? [];
  const selectTop = (n: number) =>
    setSelected(new Set(tray.slice(0, n).map((c) => c.application_id)));
  const selectAbove = (score: number) =>
    setSelected(
      new Set(tray.filter((c) => (c.score ?? 0) >= score).map((c) => c.application_id)),
    );

  const boardHasCards = useMemo(
    () => Object.values(board?.columns ?? {}).some((cards) => cards.length > 0),
    [board],
  );

  if (error && !board) {
    return (
      <main>
        <div className="notice bad">Bağlantı hatası: {error}</div>
      </main>
    );
  }
  if (!board) return <main><div className="empty">Yükleniyor…</div></main>;

  return (
    <main>
      <p className="tiny" style={{ marginBottom: 6 }}>
        <Link href={`/jobs/${jobId}`}>← {board.job_title}</Link>
      </p>
      <h1 className="page-title">Aday akışı</h1>
      <p className="page-sub">
        Değerlendirmeden geçen adayları seçin, görüşme sürecini sürükleyerek yönetin. Her
        hareket adayın zaman çizelgesine işlenir.
      </p>

      {error && <div className="notice bad" style={{ marginBottom: 16 }}>{error}</div>}

      {tray.length > 0 && (
        <section className="card" style={{ marginBottom: 28 }}>
          <div className="hstack" style={{ justifyContent: "space-between", marginBottom: 10 }}>
            <strong>Değerlendirmeden geçenler</strong>
            <span className="tiny">{tray.length} aday sırada</span>
          </div>
          <div className="hstack" style={{ marginBottom: 12 }}>
            <button className="btn tray-quick" onClick={() => selectAbove(80)}>
              80+ puan
            </button>
            <button className="btn tray-quick" onClick={() => selectAbove(60)}>
              60+ puan
            </button>
            <button className="btn tray-quick" onClick={() => selectTop(10)}>
              İlk 10
            </button>
            <button
              className="btn tray-quick"
              onClick={() =>
                setSelected((prev) =>
                  prev.size === tray.length
                    ? new Set()
                    : new Set(tray.map((c) => c.application_id)),
                )
              }
            >
              {selected.size === tray.length ? "Seçimi bırak" : "Tümü"}
            </button>
            <span className="spacer" style={{ marginLeft: "auto" }} />
            <button
              className="btn"
              disabled={selected.size === 0}
              onClick={() =>
                setComposing({
                  ids: [...selected],
                  title: `${selected.size} adaya mesaj`,
                })
              }
            >
              Mesaj yaz{selected.size > 0 ? ` (${selected.size})` : ""}
            </button>
            <button
              className="btn btn-primary"
              disabled={selected.size === 0 || moving}
              onClick={shortlistSelected}
            >
              Kısa listeye taşı{selected.size > 0 ? ` (${selected.size})` : ""}
            </button>
          </div>
          <div className="tray-list">
            {tray.map((c) => (
              <label
                key={c.application_id}
                className={`tray-row ${selected.has(c.application_id) ? "picked" : ""}`}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/plain", c.application_id);
                  e.dataTransfer.effectAllowed = "move";
                  setDragging(c.application_id);
                }}
                onDragEnd={() => setDragging(null)}
              >
                <input
                  type="checkbox"
                  checked={selected.has(c.application_id)}
                  onChange={(e) =>
                    setSelected((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(c.application_id);
                      else next.delete(c.application_id);
                      return next;
                    })
                  }
                />
                <span className="tray-name">{c.candidate_name ?? NO_NAME}</span>
                {c.band && <span className={bandClass(c.band)}>{BAND_LABEL[c.band]}</span>}
                <span className="score-pill">{scoreText(c.score)}</span>
              </label>
            ))}
          </div>
          <p className="tiny" style={{ marginTop: 10 }}>
            İpucu: adayları tek tek panodaki herhangi bir sütuna da sürükleyebilirsiniz.
          </p>
        </section>
      )}

      {tray.length === 0 && !boardHasCards && (
        <div className="card">
          <div className="empty">
            Henüz akışta aday yok. Önce{" "}
            <Link href={`/jobs/${jobId}`}>değerlendirme çalıştırın</Link>; geçen adaylar burada
            listelenir.
          </div>
        </div>
      )}

      <div className="board-bleed">
        <div className="board">
          {board.stages.map((stage) => (
            <section
              key={stage}
              className={`board-col ${dragOver === stage ? "over" : ""} ${
                stage === "hired" ? "col-good" : stage === "dropped" ? "col-muted" : ""
              }`}
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                setDragOver(stage);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(null);
              }}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(null);
                setDragging(null);
                const id = e.dataTransfer.getData("text/plain");
                if (id) moveCard(id, stage);
              }}
            >
              <header className="board-col-head">
                <span>{PIPELINE_STAGE_LABEL[stage]}</span>
                <span className="hstack" style={{ gap: 6 }}>
                  {(board.columns[stage]?.length ?? 0) > 0 && (
                    <button
                      className="col-mail"
                      title={`${PIPELINE_STAGE_LABEL[stage]} aşamasındaki adaylara mesaj yaz`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setComposing({
                          ids: (board.columns[stage] ?? []).map((c) => c.application_id),
                          title: `${PIPELINE_STAGE_LABEL[stage]} — ${
                            board.columns[stage]?.length ?? 0
                          } adaya mesaj`,
                        });
                      }}
                    >
                      ✉
                    </button>
                  )}
                  <span className="board-count">{board.columns[stage]?.length ?? 0}</span>
                </span>
              </header>
              <div className="board-cards">
                {(board.columns[stage] ?? []).map((c) => (
                  <article
                    key={c.application_id}
                    className={`pcard ${dragging === c.application_id ? "dragging" : ""}`}
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData("text/plain", c.application_id);
                      e.dataTransfer.effectAllowed = "move";
                      setDragging(c.application_id);
                    }}
                    onDragEnd={() => setDragging(null)}
                    onClick={() => setDrawerId(c.application_id)}
                  >
                    <div className="pcard-top">
                      <span className="pcard-name">{c.candidate_name ?? NO_NAME}</span>
                      <span className="score-pill">{scoreText(c.score)}</span>
                    </div>
                    {c.next_action && (
                      <div className={`pcard-next ${isOverdue(c.next_action_at) ? "late" : ""}`}>
                        {c.next_action}
                        {c.next_action_at && <> · {formatDate(c.next_action_at)}</>}
                      </div>
                    )}
                    {c.owner_name && <div className="pcard-owner">{c.owner_name}</div>}
                  </article>
                ))}
                {(board.columns[stage] ?? []).length === 0 && (
                  <div className="board-hint">Buraya sürükleyin</div>
                )}
              </div>
            </section>
          ))}
        </div>
      </div>

      {insights && <InsightsPanel insights={insights} />}

      {composing && (
        <MessageComposer
          applicationIds={composing.ids}
          title={composing.title}
          onClose={() => setComposing(null)}
          onSent={reload}
        />
      )}

      {drawerId && (
        <CandidateDrawer
          applicationId={drawerId}
          members={board.members}
          stages={board.stages}
          onClose={() => setDrawerId(null)}
          onChanged={reload}
          onCompose={(id) => {
            setDrawerId(null);
            setComposing({ ids: [id], title: "Adaya mesaj" });
          }}
        />
      )}
    </main>
  );
}

/* ------------------------------------------------------------------------- */

function bucketLabel(from: number | null, to: number | null): string {
  if (from == null) return `${to} altı`;
  if (to == null) return `${from}+`;
  return `${from}–${to - 1}`;
}

/** What this job's own corrections and outcomes say about its screening.
 *  Silent until there is enough evidence to be worth reading. */
function InsightsPanel({ insights }: { insights: JobInsights }) {
  const { calibration, insights: lines, min_sample: minSample } = insights;
  const hasBuckets = calibration.buckets.some((b) => b.advanced > 0);
  if (lines.length === 0 && !hasBuckets) return null;

  return (
    <section className="card" style={{ marginTop: 28 }}>
      <div className="hstack" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <strong>Bu ilanda öğrendiklerimiz</strong>
        <span className="tiny">{calibration.sample_size} aday üzerinden</span>
      </div>

      {lines.map((line, index) => (
        <div
          key={index}
          className={`notice ${line.severity === "notable" ? "warn" : "accent"}`}
          style={{ marginBottom: 8 }}
        >
          {line.message_tr}
        </div>
      ))}

      {hasBuckets && (
        <div className="stack" style={{ gap: 4, marginTop: 12 }}>
          <span className="field-label">Puana göre süreç ilerleyişi</span>
          {calibration.buckets.map((bucket) => (
            <div key={`${bucket.from}-${bucket.to}`} className="calib-row">
              <span className="calib-band mono">{bucketLabel(bucket.from, bucket.to)}</span>
              <span className="calib-track">
                <span
                  className="calib-fill"
                  style={{ width: `${Math.round(bucket.advance_rate * 100)}%` }}
                />
              </span>
              <span className="tiny">
                {bucket.advanced}/{bucket.count} temas
                {bucket.hired > 0 && ` · ${bucket.hired} işe alım`}
              </span>
            </div>
          ))}
        </div>
      )}

      {calibration.sample_size < minSample && (
        <p className="tiny" style={{ marginTop: 10 }}>
          Henüz az veri var — bu sayılar ilan ilerledikçe anlamlanır.
        </p>
      )}
    </section>
  );
}

function CandidateDrawer({
  applicationId,
  members,
  stages,
  onClose,
  onChanged,
  onCompose,
}: {
  applicationId: string;
  members: { id: string; name: string }[];
  stages: PipelineStage[];
  onClose: () => void;
  onChanged: () => void;
  onCompose: (applicationId: string) => void;
}) {
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [messages, setMessages] = useState<SentMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionKind, setActionKind] = useState<"note" | "meeting" | "contact">("note");
  const [note, setNote] = useState("");
  const [meetingAt, setMeetingAt] = useState("");
  const [contactResult, setContactResult] = useState<"positive" | "negative">("positive");
  const [nextAction, setNextAction] = useState("");
  const [nextActionAt, setNextActionAt] = useState("");
  const [saving, setSaving] = useState(false);
  const seeded = useRef(false);

  const load = useCallback(() => {
    api
      .sentMessages(applicationId)
      .then((m) => setMessages(m.messages))
      .catch(() => setMessages([]));
    api
      .timeline(applicationId)
      .then((t) => {
        setData(t);
        if (!seeded.current) {
          // Seed the reminder form once; later reloads must not clobber typing.
          setNextAction(t.next_action ?? "");
          setNextActionAt(t.next_action_at ? t.next_action_at.slice(0, 16) : "");
          seeded.current = true;
        }
      })
      .catch((e: ApiError) => setError(e.message));
  }, [applicationId]);

  useEffect(load, [load]);

  const saveEvent = () => {
    if (actionKind === "meeting" && !meetingAt) {
      setError("Görüşme için tarih seçin.");
      return;
    }
    setSaving(true);
    setError(null);
    api
      .addPipelineEvent(applicationId, {
        kind: actionKind,
        note: note.trim() || (actionKind === "meeting" ? "Görüşme" : null),
        occurs_at:
          actionKind === "meeting" && meetingAt ? new Date(meetingAt).toISOString() : null,
        detail: actionKind === "contact" ? { result: contactResult } : {},
      })
      .then(() => {
        setNote("");
        setMeetingAt("");
        load();
        onChanged();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setSaving(false));
  };

  const saveReminder = (clear: boolean) => {
    setSaving(true);
    setError(null);
    api
      .updateApplication(applicationId, {
        next_action: clear ? null : nextAction.trim() || null,
        next_action_at:
          clear || !nextActionAt ? null : new Date(nextActionAt).toISOString(),
      })
      .then(() => {
        if (clear) {
          setNextAction("");
          setNextActionAt("");
        }
        load();
        onChanged();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setSaving(false));
  };

  const setOwner = (ownerId: string) => {
    api
      .updateApplication(applicationId, { owner_id: ownerId || null })
      .then(() => {
        load();
        onChanged();
      })
      .catch((e: ApiError) => setError(e.message));
  };

  const setStage = (stage: string) => {
    api
      .moveStage(applicationId, stage)
      .then(() => {
        load();
        onChanged();
      })
      .catch((e: ApiError) => setError(e.message));
  };

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Aday süreci">
        {!data && !error && <div className="empty">Yükleniyor…</div>}
        {error && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
        {data && (
          <>
            <div className="hstack" style={{ justifyContent: "space-between" }}>
              <h2 style={{ margin: 0, fontSize: 19 }}>{data.candidate_name ?? NO_NAME}</h2>
              <button className="btn btn-ghost" onClick={onClose}>
                Kapat
              </button>
            </div>
            <div className="hstack" style={{ marginTop: 6 }}>
              {data.band && <span className={bandClass(data.band)}>{BAND_LABEL[data.band]}</span>}
              {data.score != null && <span className="chip">Puan {scoreText(data.score)}</span>}
              {data.candidate_email && <span className="tiny">{data.candidate_email}</span>}
            </div>

            <hr className="divider" />

            <div className="drawer-grid">
              <label className="field-label" htmlFor="stage-select">
                Aşama
              </label>
              <select
                id="stage-select"
                className="select"
                value={data.stage}
                onChange={(e) => setStage(e.target.value)}
              >
                {data.stage === "new" && <option value="new">Yeni</option>}
                {stages.map((s) => (
                  <option key={s} value={s}>
                    {PIPELINE_STAGE_LABEL[s]}
                  </option>
                ))}
              </select>

              <label className="field-label" htmlFor="owner-select">
                Sorumlu
              </label>
              <select
                id="owner-select"
                className="select"
                value={data.owner_id ?? ""}
                onChange={(e) => setOwner(e.target.value)}
              >
                <option value="">Atanmadı</option>
                {members.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>

            <hr className="divider" />

            <div className="hstack seg" role="tablist" aria-label="Kayıt türü">
              {(
                [
                  ["note", "Not"],
                  ["meeting", "Görüşme planla"],
                  ["contact", "Temas"],
                ] as const
              ).map(([kind, label]) => (
                <button
                  key={kind}
                  role="tab"
                  aria-selected={actionKind === kind}
                  className={`btn seg-btn ${actionKind === kind ? "on" : ""}`}
                  onClick={() => setActionKind(kind)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="stack" style={{ marginTop: 10 }}>
              {actionKind === "meeting" && (
                <input
                  type="datetime-local"
                  className="input"
                  value={meetingAt}
                  onChange={(e) => setMeetingAt(e.target.value)}
                  aria-label="Görüşme tarihi"
                />
              )}
              {actionKind === "contact" && (
                <div className="hstack">
                  {(
                    [
                      ["positive", "Olumlu"],
                      ["negative", "Olumsuz"],
                    ] as const
                  ).map(([value, label]) => (
                    <label key={value} className="hstack tiny" style={{ gap: 6 }}>
                      <input
                        type="radio"
                        name="contact-result"
                        checked={contactResult === value}
                        onChange={() => setContactResult(value)}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              )}
              <textarea
                className="textarea"
                style={{ minHeight: 64 }}
                placeholder={
                  actionKind === "note"
                    ? "Kısa bir not…"
                    : actionKind === "meeting"
                      ? "Konu (ör. Teknik mülakat)"
                      : "Nasıl temas kuruldu?"
                }
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <div>
                <button className="btn btn-primary" disabled={saving} onClick={saveEvent}>
                  Kaydet
                </button>
                {actionKind === "meeting" && (
                  <span className="tiny" style={{ marginLeft: 10 }}>
                    Görüşme, adayın sonraki adımı olarak da işaretlenir.
                  </span>
                )}
              </div>
            </div>

            <hr className="divider" />

            <label className="field-label">Sonraki adım</label>
            <div className="stack">
              <input
                className="input"
                placeholder="ör. Referans kontrolü"
                value={nextAction}
                onChange={(e) => setNextAction(e.target.value)}
              />
              <input
                type="datetime-local"
                className="input"
                value={nextActionAt}
                onChange={(e) => setNextActionAt(e.target.value)}
                aria-label="Sonraki adım tarihi"
              />
              <div className="hstack">
                <button className="btn" disabled={saving} onClick={() => saveReminder(false)}>
                  Hatırlatıcıyı kaydet
                </button>
                {(data.next_action || data.next_action_at) && (
                  <button
                    className="btn btn-ghost"
                    disabled={saving}
                    onClick={() => saveReminder(true)}
                  >
                    Tamamlandı, temizle
                  </button>
                )}
              </div>
            </div>

            <hr className="divider" />

            <div className="hstack" style={{ justifyContent: "space-between" }}>
              <span className="field-label" style={{ marginBottom: 0 }}>
                Gönderilen mesajlar
              </span>
              <button className="btn" onClick={() => onCompose(applicationId)}>
                Mesaj yaz
              </button>
            </div>
            {messages.length === 0 ? (
              <p className="tiny" style={{ marginTop: 6 }}>
                Bu adaya henüz yazılmadı.
              </p>
            ) : (
              <div className="stack" style={{ gap: 6, marginTop: 8 }}>
                {messages.map((m) => (
                  <div key={m.id} className="file-row">
                    <span className="file-name">{m.subject}</span>
                    <span className="tiny">{formatDate(m.created_at)}</span>
                    <span className={`chip ${m.status === "failed" ? "bad" : "good"}`}>
                      {m.status === "failed" ? "gönderilemedi" : "gönderildi"}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <hr className="divider" />

            <h3 style={{ fontSize: 15, margin: "0 0 10px" }}>Zaman çizelgesi</h3>
            {data.events.length === 0 && <div className="empty">Henüz kayıt yok.</div>}
            <ol className="timeline">
              {data.events.map((e) => (
                <li key={e.id} className="timeline-row">
                  <div className="hstack" style={{ gap: 8 }}>
                    <span className="chip">{EVENT_KIND_LABEL[e.kind] ?? e.kind}</span>
                    {e.kind === "stage_change" && (
                      <span className="tiny">
                        {PIPELINE_STAGE_LABEL[e.from_stage ?? ""] ?? e.from_stage} →{" "}
                        {PIPELINE_STAGE_LABEL[e.to_stage ?? ""] ?? e.to_stage}
                      </span>
                    )}
                    {e.detail.result === "positive" && <span className="chip good">Olumlu</span>}
                    {e.detail.result === "negative" && <span className="chip bad">Olumsuz</span>}
                    <span className="tiny" style={{ marginLeft: "auto" }}>
                      {formatDate(e.created_at)}
                    </span>
                  </div>
                  {e.note && <div className="timeline-note">{e.note}</div>}
                  {e.occurs_at && (
                    <div className="tiny">Planlanan: {formatDate(e.occurs_at)}</div>
                  )}
                  {e.actor_name && <div className="tiny">{e.actor_name}</div>}
                </li>
              ))}
            </ol>
          </>
        )}
      </aside>
    </>
  );
}
