"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function ResetPasswordPage() {
  const { token } = useParams<{ token: string }>();
  const [emailMasked, setEmailMasked] = useState<string | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .passwordResetLookup(token)
      .then((r) => setEmailMasked(r.email_masked))
      .catch((err: ApiError) =>
        setLookupError(
          err.status === 410
            ? "Bu bağlantının süresi dolmuş veya bağlantı zaten kullanılmış. Yeni bir bağlantı isteyin."
            : "Bağlantı bulunamadı. Yeni bir sıfırlama bağlantısı isteyin.",
        ),
      );
  }, [token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.resetPassword(token, password);
      window.location.assign("/");
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="auth-wrap">
      <div className="card auth-card">
        {lookupError && (
          <>
            <h1>Bağlantı geçersiz</h1>
            <p className="auth-sub">{lookupError}</p>
            <div className="auth-links">
              <span>
                <Link href="/forgot-password">Yeni bağlantı iste</Link>
              </span>
              <span>
                <Link href="/login">Giriş sayfasına dön</Link>
              </span>
            </div>
          </>
        )}

        {!lookupError && !emailMasked && <p className="auth-sub">Bağlantı kontrol ediliyor…</p>}

        {emailMasked && (
          <form onSubmit={submit}>
            <h1>Yeni şifre belirleyin</h1>
            <p className="auth-sub">
              <strong>{emailMasked}</strong> hesabı için yeni bir şifre belirliyorsunuz. Diğer
              tüm oturumlarınız güvenlik için kapatılacak.
            </p>

            {error && <div className="notice bad auth-field">{error}</div>}

            <div className="auth-field">
              <label className="field-label" htmlFor="password">
                Yeni şifre
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
              {busy ? "Kaydediliyor…" : "Şifreyi değiştir ve giriş yap"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
