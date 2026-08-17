"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { InvitationLookup } from "@/lib/types";

export default function JoinPage() {
  const { token } = useParams<{ token: string }>();
  const [invitation, setInvitation] = useState<InvitationLookup | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .invitationLookup(token)
      .then(setInvitation)
      .catch((err: ApiError) =>
        setLookupError(
          err.status === 410
            ? "Bu davet bağlantısının süresi dolmuş veya bağlantı zaten kullanılmış."
            : "Davet bulunamadı. Bağlantıyı kontrol edin veya sizi davet eden kişiden yenisini isteyin.",
        ),
      );
  }, [token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.acceptInvitation(token, { name: name.trim(), password });
      window.location.assign("/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Bu e-posta ile zaten bir hesap var. Giriş yapmayı deneyin."
          : (err as Error).message,
      );
      setBusy(false);
    }
  }

  return (
    <main className="auth-wrap">
      <div className="card auth-card">
        {lookupError && (
          <>
            <h1>Davet geçersiz</h1>
            <p className="auth-sub">{lookupError}</p>
            <div className="auth-links">
              <span>
                Hesabınız var mı? <Link href="/login">Giriş yapın</Link>
              </span>
            </div>
          </>
        )}

        {!lookupError && !invitation && <p className="auth-sub">Davet kontrol ediliyor…</p>}

        {invitation && (
          <form onSubmit={submit}>
            <h1>Ekibe katılın</h1>
            <p className="auth-sub">
              <strong>{invitation.invited_by}</strong> sizi{" "}
              <strong>{invitation.org_name}</strong> çalışma alanına davet etti. Hesabınız{" "}
              <strong>{invitation.email}</strong> için oluşturulacak.
            </p>

            {error && <div className="notice bad auth-field">{error}</div>}

            <div className="auth-field">
              <label className="field-label" htmlFor="name">
                Adınız
              </label>
              <input
                id="name"
                className="input"
                autoComplete="name"
                required
                minLength={2}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="auth-field">
              <label className="field-label" htmlFor="password">
                Şifre belirleyin
              </label>
              <input
                id="password"
                className="input"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <span className="tiny">En az 8 karakter.</span>
            </div>

            <button className="btn btn-primary auth-submit" disabled={busy} type="submit">
              {busy ? "Katılınıyor…" : "Katıl"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
