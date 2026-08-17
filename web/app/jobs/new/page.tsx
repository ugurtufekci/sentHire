"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Template } from "@/lib/types";

export default function NewJobPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [title, setTitle] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listTemplates().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  async function create() {
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const job = await api.createJob(title.trim(), selected);
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <main>
      <h1 className="page-title">Yeni ilan</h1>
      <p className="page-sub">
        Bir şablon seçerseniz rolün standart kriterleri hazır gelir; sonra kendi cümlelerinizle
        istediğiniz kadar ekleyip değiştirebilirsiniz.
      </p>

      <div className="stack" style={{ maxWidth: 560 }}>
        <div>
          <label className="field-label" htmlFor="job-title">
            İlan başlığı
          </label>
          <input
            id="job-title"
            className="input"
            placeholder="ör. Satış Uzmanı — Ankara"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>

        <div>
          <span className="field-label">Şablon (isteğe bağlı)</span>
          <div className="stack">
            <button
              type="button"
              className="card quiet row-hover"
              style={{ textAlign: "left", cursor: "pointer", borderColor: selected === null ? "var(--accent)" : undefined }}
              onClick={() => setSelected(null)}
            >
              <strong>Boş başla</strong>
              <div className="tiny">Tüm kriterleri kendiniz anlatın</div>
            </button>
            {templates.map((t) => (
              <button
                key={t.slug}
                type="button"
                className="card quiet row-hover"
                style={{ textAlign: "left", cursor: "pointer", borderColor: selected === t.slug ? "var(--accent)" : undefined }}
                onClick={() => setSelected(t.slug)}
              >
                <strong>{t.title}</strong>
                <div className="tiny">{t.requirement_count} hazır kriter</div>
              </button>
            ))}
          </div>
        </div>

        {error && <div className="notice bad">{error}</div>}

        <div>
          <button className="btn btn-primary" onClick={create} disabled={busy || !title.trim()}>
            {busy ? "Oluşturuluyor…" : "İlanı oluştur"}
          </button>
        </div>
      </div>
    </main>
  );
}
