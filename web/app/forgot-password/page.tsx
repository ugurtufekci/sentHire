"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-wrap">
      <div className="card auth-card">
        {sent ? (
          <>
            <h1>E-postanızı kontrol edin</h1>
            <p className="auth-sub">
              Bu adres kayıtlıysa, <strong>{email.trim()}</strong> adresine bir sıfırlama
              bağlantısı gönderdik. Bağlantı 1 saat geçerlidir; gelen kutunuzda göremezseniz
              spam klasörüne bakın.
            </p>
            <div className="auth-links">
              <span>
                <Link href="/login">Giriş sayfasına dön</Link>
              </span>
            </div>
          </>
        ) : (
          <form onSubmit={submit}>
            <h1>Şifrenizi sıfırlayın</h1>
            <p className="auth-sub">
              Hesabınızın e-posta adresini girin; size yeni şifre belirlemeniz için bir
              bağlantı gönderelim.
            </p>

            {error && <div className="notice bad auth-field">{error}</div>}

            <div className="auth-field">
              <label className="field-label" htmlFor="email">
                E-posta
              </label>
              <input
                id="email"
                className="input"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <button className="btn btn-primary auth-submit" disabled={busy} type="submit">
              {busy ? "Gönderiliyor…" : "Sıfırlama bağlantısı gönder"}
            </button>

            <div className="auth-links">
              <span>
                Şifrenizi hatırladınız mı? <Link href="/login">Giriş yapın</Link>
              </span>
            </div>
          </form>
        )}
      </div>
    </main>
  );
}
