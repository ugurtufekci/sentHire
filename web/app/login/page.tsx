"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(email.trim(), password);
      window.location.assign("/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "E-posta veya şifre hatalı."
          : (err as Error).message,
      );
      setBusy(false);
    }
  }

  return (
    <main className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Tekrar hoş geldiniz</h1>
        <p className="auth-sub">Çalışma alanınıza giriş yapın.</p>

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
        <div className="auth-field">
          <div className="hstack" style={{ justifyContent: "space-between" }}>
            <label className="field-label" htmlFor="password">
              Şifre
            </label>
            <Link href="/forgot-password" className="tiny" style={{ color: "var(--accent)" }}>
              Şifrenizi mi unuttunuz?
            </Link>
          </div>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <button className="btn btn-primary auth-submit" disabled={busy} type="submit">
          {busy ? "Giriş yapılıyor…" : "Giriş yap"}
        </button>

        <div className="auth-links">
          <span>
            Şirketiniz henüz kayıtlı değil mi? <Link href="/signup">Çalışma alanı oluşturun</Link>
          </span>
          <span className="tiny">
            Ekibinize davet edildiyseniz, size gönderilen davet bağlantısını kullanın.
          </span>
        </div>
      </form>
    </main>
  );
}
