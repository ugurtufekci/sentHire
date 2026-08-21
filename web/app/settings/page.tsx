"use client";

/** Ayarlar — profil, çalışma alanı, aday mesaj şablonları.
 *
 * One page, three cards, no tabs: an MVP settings screen should be readable
 * top to bottom in one scroll, and each card saves itself so a failed save in
 * one never eats the edits in another.
 */

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { MessageTemplate } from "@/lib/types";

function useSave() {
  const [state, setState] = useState<"idle" | "busy" | "done" | string>("idle");
  const run = async (fn: () => Promise<unknown>) => {
    setState("busy");
    try {
      await fn();
      setState("done");
      setTimeout(() => setState("idle"), 2500);
    } catch (e) {
      setState(e instanceof ApiError ? e.message : (e as Error).message);
    }
  };
  return { state, run };
}

function SaveRow({ state, label = "Kaydet" }: { state: string; label?: string }) {
  return (
    <div className="hstack">
      <button className="btn btn-primary" type="submit" disabled={state === "busy"}>
        {label}
      </button>
      {state === "done" && <span className="tiny saved">Kaydedildi ✓</span>}
      {state !== "idle" && state !== "busy" && state !== "done" && (
        <span className="tiny" style={{ color: "var(--bad)" }}>
          {state}
        </span>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const { session, isAdmin, refresh } = useSession();

  const [name, setName] = useState("");
  const profile = useSave();

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const password = useSave();

  const [orgName, setOrgName] = useState("");
  const org = useSave();

  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [variables, setVariables] = useState<string[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const template = useSave();

  useEffect(() => {
    if (session) {
      setName(session.user.name ?? "");
      setOrgName(session.org.name);
    }
  }, [session]);

  useEffect(() => {
    api
      .messageTemplates()
      .then((data) => {
        setTemplates(data.templates);
        setVariables(data.variables);
      })
      .catch(() => setTemplates([]));
  }, []);

  const openTemplate = (t: MessageTemplate) => {
    setEditing(t.slug);
    setSubject(t.subject);
    setBody(t.body);
  };

  return (
    <main>
      <h1 className="page-title">Ayarlar</h1>
      <p className="page-sub">
        Profiliniz, çalışma alanınız ve adaylara giden mesajların şablonları.
      </p>

      <section className="card">
        <span className="field-label">Profil</span>
        <form
          className="stack"
          style={{ maxWidth: 420 }}
          onSubmit={(e) => {
            e.preventDefault();
            profile.run(async () => {
              await api.updateProfile(name.trim());
              await refresh();
            });
          }}
        >
          <div>
            <label className="field-label" htmlFor="profile-name">
              Adınız
            </label>
            <input
              id="profile-name"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              minLength={1}
            />
          </div>
          <div>
            <span className="field-label">E-posta</span>
            <div className="tiny">
              {session ? session.user.email : "…"} — değiştirilemez
            </div>
          </div>
          <SaveRow state={profile.state} />
        </form>
      </section>

      <section className="card">
        <span className="field-label">Parola değiştir</span>
        <form
          className="stack"
          style={{ maxWidth: 420 }}
          onSubmit={(e) => {
            e.preventDefault();
            password.run(async () => {
              await api.changePassword(currentPassword, newPassword);
              setCurrentPassword("");
              setNewPassword("");
            });
          }}
        >
          <div>
            <label className="field-label" htmlFor="current-password">
              Mevcut parola
            </label>
            <input
              id="current-password"
              type="password"
              className="input"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          <div>
            <label className="field-label" htmlFor="new-password">
              Yeni parola (en az 10 karakter)
            </label>
            <input
              id="new-password"
              type="password"
              className="input"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              required
            />
          </div>
          <p className="tiny" style={{ margin: 0 }}>
            Parolanız değişince diğer tüm cihazlardaki oturumlar kapatılır; bu
            oturum açık kalır.
          </p>
          <SaveRow state={password.state} label="Parolayı değiştir" />
        </form>
      </section>

      {isAdmin && (
        <section className="card">
          <span className="field-label">Çalışma alanı</span>
          <form
            className="stack"
            style={{ maxWidth: 420 }}
            onSubmit={(e) => {
              e.preventDefault();
              org.run(async () => {
                await api.renameOrg(orgName.trim());
                await refresh();
              });
            }}
          >
            <div>
              <label className="field-label" htmlFor="org-name">
                Şirket adı — adaylara giden mesajlarda bu ad görünür
              </label>
              <input
                id="org-name"
                className="input"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                required
                minLength={2}
              />
            </div>
            <SaveRow state={org.state} />
          </form>
        </section>
      )}

      <section className="card">
        <div className="hstack" style={{ justifyContent: "space-between" }}>
          <span className="field-label" style={{ marginBottom: 0 }}>
            Aday mesaj şablonları
          </span>
          {variables.length > 0 && (
            <span className="tiny">
              Alanlar: {variables.map((v) => `{{${v}}}`).join(" ")}
            </span>
          )}
        </div>
        <p className="tiny" style={{ margin: "6px 0 12px" }}>
          Şablonu düzenlemek geçmişi değiştirmez — daha önce gönderilen mesajlar
          gönderildiği haliyle kayıtlı kalır.
        </p>
        <div className="stack" style={{ gap: 8 }}>
          {templates.map((t) => (
            <div key={t.slug} className="req-card">
              <div className="hstack" style={{ justifyContent: "space-between" }}>
                <span className="req-title">{t.name}</span>
                {editing === t.slug ? (
                  <button className="btn btn-ghost" onClick={() => setEditing(null)}>
                    Vazgeç
                  </button>
                ) : (
                  <button className="btn btn-ghost" onClick={() => openTemplate(t)}>
                    Düzenle
                  </button>
                )}
              </div>
              {editing === t.slug ? (
                <form
                  className="stack"
                  onSubmit={(e) => {
                    e.preventDefault();
                    template.run(async () => {
                      const saved = await api.saveTemplate(t.slug, { subject, body });
                      setTemplates((prev) =>
                        prev.map((x) => (x.slug === t.slug ? saved : x)),
                      );
                      setEditing(null);
                    });
                  }}
                >
                  <input
                    className="input"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    aria-label="Konu"
                    required
                  />
                  <textarea
                    className="textarea"
                    style={{ minHeight: 180 }}
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    aria-label="Mesaj"
                    required
                  />
                  <SaveRow state={template.state} />
                </form>
              ) : (
                <div className="tiny" style={{ whiteSpace: "pre-wrap" }}>
                  <strong>{t.subject}</strong>
                  {"\n"}
                  {t.body.length > 180 ? t.body.slice(0, 180) + "…" : t.body}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
