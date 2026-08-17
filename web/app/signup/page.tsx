"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function SignupPage() {
  const [companyName, setCompanyName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.signup({
        company_name: companyName.trim(),
        name: name.trim(),
        email: email.trim(),
        password,
      });
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
      <form className="card auth-card" onSubmit={submit}>
        <h1>Çalışma alanınızı oluşturun</h1>
        <p className="auth-sub">
          Şirketiniz için tek bir alan açılır; ekip arkadaşlarınızı daha sonra davet edersiniz.
          Herkes aynı ilanları ve sonuçları görür.
        </p>

        {error && <div className="notice bad auth-field">{error}</div>}

        <div className="auth-field">
          <label className="field-label" htmlFor="company">
            Şirket adı
          </label>
          <input
            id="company"
            className="input"
            autoComplete="organization"
            required
            minLength={2}
            placeholder="ör. Aksa Teknoloji"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
          />
        </div>
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
          <label className="field-label" htmlFor="email">
            İş e-postanız
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
          <label className="field-label" htmlFor="password">
            Şifre
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
          {busy ? "Oluşturuluyor…" : "Çalışma alanını oluştur"}
        </button>

        <div className="auth-links">
          <span>
            Zaten hesabınız var mı? <Link href="/login">Giriş yapın</Link>
          </span>
        </div>
      </form>
    </main>
  );
}
