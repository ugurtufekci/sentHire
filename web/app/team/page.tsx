"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Member, PendingInvitation, Role } from "@/lib/types";

const ROLE_LABEL: Record<string, string> = { admin: "Yönetici", member: "Üye" };

export default function TeamPage() {
  const { session: me } = useSession();
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<PendingInvitation[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("member");
  const [createdLink, setCreatedLink] = useState<{
    email: string;
    url: string;
    emailQueued: boolean;
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isAdmin = me ? me.user.role === "admin" : false;

  const refresh = useCallback(async (admin: boolean) => {
    setMembers(await api.listMembers());
    if (admin) setInvitations(await api.listInvitations());
  }, []);

  useEffect(() => {
    if (!me) return;
    refresh(me.user.role === "admin").catch((e) => setError((e as Error).message));
  }, [me, refresh]);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCreatedLink(null);
    try {
      const created = await api.createInvitation(inviteEmail.trim(), inviteRole);
      setCreatedLink({
        email: created.email,
        url: created.invite_url,
        emailQueued: created.email_queued,
      });
      setCopied(false);
      setInviteEmail("");
      await refresh(true);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Bu e-posta zaten üye veya bekleyen bir daveti var."
          : (err as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    if (!createdLink) return;
    try {
      await navigator.clipboard.writeText(createdLink.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      /* the link stays visible for manual copying */
    }
  }

  async function revoke(id: string) {
    await api.revokeInvitation(id);
    await refresh(true);
  }

  async function resend(id: string) {
    setError(null);
    try {
      const updated = await api.resendInvitation(id);
      setCreatedLink({
        email: updated.email,
        url: updated.invite_url,
        emailQueued: updated.email_queued,
      });
      setCopied(false);
      await refresh(true);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function patchMember(id: string, patch: { role?: Role; is_active?: boolean }) {
    setError(null);
    try {
      await api.updateMember(id, patch);
      await refresh(true);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main>
      <h1 className="page-title">Ekip</h1>
      <p className="page-sub">
        {me ? `${me.org.name} çalışma alanı. ` : ""}
        Buradaki herkes aynı ilanları, adayları ve sonuçları görür; yöneticiler ekip arkadaşlarını
        davet edebilir.
      </p>

      {error && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}

      {isAdmin && (
        <div className="card" style={{ marginBottom: 12 }}>
          <span className="field-label">Ekip arkadaşınızı davet edin</span>
          <form className="hstack" onSubmit={invite}>
            <input
              className="input"
              style={{ maxWidth: 320 }}
              type="email"
              required
              placeholder="isim@sirketiniz.com"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              aria-label="Davet edilecek e-posta"
            />
            <select
              className="select"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as Role)}
              aria-label="Rol"
            >
              <option value="member">Üye</option>
              <option value="admin">Yönetici</option>
            </select>
            <button className="btn btn-primary" disabled={busy} type="submit">
              {busy ? "Oluşturuluyor…" : "Davet bağlantısı oluştur"}
            </button>
          </form>

          {createdLink && (
            <div className="notice accent" style={{ marginTop: 12 }}>
              <div className="hstack" style={{ justifyContent: "space-between" }}>
                <span>
                  {createdLink.emailQueued ? (
                    <>
                      <strong>{createdLink.email}</strong> adresine davet e-postası gönderildi.
                      Dilerseniz bağlantıyı kopyalayıp kendiniz de iletebilirsiniz.
                    </>
                  ) : (
                    <>
                      Davet hazır ancak e-posta gönderilemedi — bağlantıyı kopyalayıp{" "}
                      <strong>{createdLink.email}</strong> adresine kendiniz iletin.
                    </>
                  )}{" "}
                  Güvenlik için bağlantı yalnızca şimdi görünür.
                </span>
                <button className="btn" type="button" onClick={copyLink}>
                  {copied ? "Kopyalandı ✓" : "Bağlantıyı kopyala"}
                </button>
              </div>
              <div className="mono tiny" style={{ marginTop: 8, wordBreak: "break-all" }}>
                {createdLink.url}
              </div>
            </div>
          )}

          {invitations.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <span className="field-label">Bekleyen davetler</span>
              {invitations.map((inv) => (
                <div key={inv.id} className="file-row">
                  <span className="file-name">{inv.email}</span>
                  <span className="chip">{ROLE_LABEL[inv.role]}</span>
                  <span className="tiny">son geçerlilik {formatDate(inv.expires_at)}</span>
                  <button className="btn btn-ghost" onClick={() => resend(inv.id)} type="button">
                    Yeniden gönder
                  </button>
                  <button className="btn btn-ghost" onClick={() => revoke(inv.id)} type="button">
                    İptal et
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card quiet">
        <span className="field-label">Üyeler ({members.filter((m) => m.is_active).length})</span>
        <div className="table-scroll">
          <table className="soft-table">
            <thead>
              <tr>
                <th>İsim</th>
                <th>E-posta</th>
                <th>Rol</th>
                <th>Son giriş</th>
                {isAdmin && <th aria-label="İşlemler" />}
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id} style={m.is_active ? undefined : { opacity: 0.55 }}>
                  <td>
                    {m.name || "—"}
                    {m.id === (me ? me.user.id : null) && <span className="tiny"> (siz)</span>}
                  </td>
                  <td>{m.email}</td>
                  <td>
                    <span className={m.role === "admin" ? "chip accent" : "chip"}>
                      {ROLE_LABEL[m.role]}
                    </span>
                    {!m.is_active && <span className="chip bad">Devre dışı</span>}
                  </td>
                  <td>{m.last_login_at ? formatDate(m.last_login_at) : "—"}</td>
                  {isAdmin && (
                    <td>
                      {m.id !== (me ? me.user.id : null) && (
                        <span className="hstack" style={{ justifyContent: "flex-end" }}>
                          {m.is_active && (
                            <button
                              className="btn btn-ghost"
                              type="button"
                              onClick={() =>
                                patchMember(m.id, {
                                  role: m.role === "admin" ? "member" : "admin",
                                })
                              }
                            >
                              {m.role === "admin" ? "Üye yap" : "Yönetici yap"}
                            </button>
                          )}
                          <button
                            className="btn btn-ghost"
                            type="button"
                            onClick={() => patchMember(m.id, { is_active: !m.is_active })}
                          >
                            {m.is_active ? "Devre dışı bırak" : "Aktifleştir"}
                          </button>
                        </span>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
