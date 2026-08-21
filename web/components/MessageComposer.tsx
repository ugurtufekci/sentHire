"use client";

/** Writing to candidates — one, or a shortlist, through the same panel.
 *
 * The order is deliberate: pick a template, read the letter *as the first
 * candidate will receive it*, see exactly who it goes to, then send. Nothing
 * here fires on its own, and the recipient list shows the people who cannot be
 * written to (no address on the CV) rather than quietly dropping them.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { MessagePreview, MessageTemplate, SendResult } from "@/lib/types";

// Mirrors the backend's parse_when: day-first, as Turkish dates are written.
const WHEN_PATTERN = /^\d{1,2}[./]\d{1,2}[./]\d{4}\s+\d{1,2}[:.]\d{2}$/;

export default function MessageComposer({
  applicationIds,
  title,
  onClose,
  onSent,
}: {
  applicationIds: string[];
  title: string;
  onClose: () => void;
  onSent?: () => void;
}) {
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [slug, setSlug] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [when, setWhen] = useState("");
  const [previews, setPreviews] = useState<MessagePreview[]>([]);
  const [result, setResult] = useState<SendResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    api
      .messageTemplates()
      .then((data) => {
        setTemplates(data.templates);
        const first = data.templates[0];
        if (first) {
          setSlug(first.slug);
          setSubject(first.subject);
          setBody(first.body);
        }
      })
      .catch((e: ApiError) => setError(e.message));
  }, []);

  const refreshPreview = useCallback(() => {
    if (!subject || !body) return;
    api
      .previewMessages({ application_ids: applicationIds, subject, body, when: when || null })
      .then((data) => {
        setPreviews(data.messages);
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
  }, [applicationIds, subject, body, when]);

  useEffect(() => {
    const timer = setTimeout(refreshPreview, 350); // debounce while typing
    return () => clearTimeout(timer);
  }, [refreshPreview]);

  const pickTemplate = (next: string) => {
    const template = templates.find((t) => t.slug === next);
    if (!template) return;
    setSlug(next);
    setSubject(template.subject);
    setBody(template.body);
    setResult(null);
  };

  const send = (confirmResend: boolean) => {
    setBusy(true);
    setError(null);
    api
      .sendMessages({
        application_ids: applicationIds,
        subject,
        body,
        when: when || null,
        template_slug: slug,
        confirm_resend: confirmResend,
      })
      .then((r) => {
        setResult(r);
        if (r.sent.length) onSent?.();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const sendable = previews.filter((p) => !p.blocked);
  const blocked = previews.filter((p) => p.blocked);
  const needsConfirm = (result?.skipped ?? []).some((s) => s.needs_confirmation);
  const first = sendable[0];

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Adaya mesaj">
        <div className="hstack" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0, fontSize: 19 }}>{title}</h2>
          <button className="btn btn-ghost" onClick={onClose}>
            Kapat
          </button>
        </div>

        {error && <div className="notice bad" style={{ marginTop: 12 }}>{error}</div>}

        <div className="hstack seg" style={{ marginTop: 14 }}>
          {templates.map((t) => (
            <button
              key={t.slug}
              className={`btn seg-btn ${slug === t.slug ? "on" : ""}`}
              onClick={() => pickTemplate(t.slug)}
            >
              {t.name}
            </button>
          ))}
        </div>

        <div className="stack" style={{ marginTop: 12 }}>
          <div>
            <label className="field-label" htmlFor="msg-subject">
              Konu
            </label>
            <input
              id="msg-subject"
              className="input"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </div>
          <div>
            <label className="field-label" htmlFor="msg-body">
              Mesaj — {"{{aday}}"}, {"{{ilan}}"}, {"{{sirket}}"}, {"{{gonderen}}"},{" "}
              {"{{tarih}}"} alanları her aday için doldurulur
            </label>
            <textarea
              id="msg-body"
              className="textarea"
              style={{ minHeight: 200 }}
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
          </div>
          {body.includes("{{tarih}}") && (
            <div>
              <label className="field-label" htmlFor="msg-when">
                Görüşme zamanı (mesajdaki {"{{tarih}}"} yerine yazılır)
              </label>
              <input
                id="msg-when"
                className="input"
                placeholder="ör. 25.08.2026 14:00"
                value={when}
                onChange={(e) => setWhen(e.target.value)}
              />
              {slug === "interview_invite" && (
                <span className="tiny" style={{ display: "block", marginTop: 5 }}>
                  {WHEN_PATTERN.test(when.trim())
                    ? "✓ E-postaya takvim daveti eklenecek — aday tek dokunuşla takvimine kaydeder."
                    : "GG.AA.YYYY SS:DD biçiminde yazarsanız e-postaya takvim daveti eklenir."}
                </span>
              )}
            </div>
          )}
        </div>

        <hr className="divider" />

        <span className="field-label">
          Adayın göreceği hali{first?.candidate_name ? ` — ${first.candidate_name}` : ""}
        </span>
        {first ? (
          <div className="letter">
            <div className="letter-subject">{first.subject}</div>
            <div className="letter-body">{first.body}</div>
          </div>
        ) : (
          <div className="empty">Gönderilebilecek aday yok.</div>
        )}

        <div className="hstack" style={{ marginTop: 12, justifyContent: "space-between" }}>
          <span className="tiny">
            {sendable.length} kişiye gidecek
            {blocked.length > 0 && ` · ${blocked.length} kişiye gidemez`}
          </span>
          {previews.length > 1 && (
            <button className="btn btn-ghost" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Listeyi gizle" : "Alıcıları gör"}
            </button>
          )}
        </div>

        {showAll && (
          <div className="stack" style={{ gap: 2, marginTop: 8 }}>
            {previews.map((p) => (
              <div key={p.application_id} className="file-row">
                <span className="file-name">{p.candidate_name ?? "İsimsiz aday"}</span>
                <span className="tiny">{p.to_email ?? "—"}</span>
                {p.blocked && <span className="chip warn">{p.blocked}</span>}
              </div>
            ))}
          </div>
        )}

        {blocked.length > 0 && !showAll && (
          <div className="notice warn" style={{ marginTop: 10 }}>
            {blocked.length} aday atlanacak: {blocked[0].blocked}.
          </div>
        )}

        <div className="hstack" style={{ marginTop: 16 }}>
          <button
            className="btn btn-primary"
            disabled={busy || sendable.length === 0}
            onClick={() => send(false)}
          >
            {sendable.length > 1 ? `${sendable.length} kişiye gönder` : "Gönder"}
          </button>
          <span className="tiny">Gönderilen mesaj geri alınamaz.</span>
        </div>

        {result && (
          <div className="stack" style={{ marginTop: 14, gap: 8 }}>
            {result.sent.length > 0 && (
              <div className="notice accent">
                {result.sent.length} mesaj gönderildi
                {result.calendar_attached ? ", takvim davetiyle birlikte" : ""}. Adaylar
                &ldquo;Temas kuruldu&rdquo; aşamasına taşındı.
              </div>
            )}
            {result.skipped.map((s) => (
              <div key={s.application_id} className="notice warn">
                Atlandı: {s.reason}
              </div>
            ))}
            {needsConfirm && (
              <button className="btn" disabled={busy} onClick={() => send(true)}>
                Yine de tekrar gönder
              </button>
            )}
          </div>
        )}
      </aside>
    </>
  );
}
