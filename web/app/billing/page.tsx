"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { BillingDetails, BillingInfo, Me } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  trial: "Deneme",
  pending_checkout: "Ödeme bekleniyor",
  active: "Aktif",
  past_due: "Ödeme gecikti",
  canceled: "İptal edildi",
};

const EMPTY_DETAILS: BillingDetails = {
  company_title: "",
  tax_number: "",
  tax_office: "",
  address: "",
  city: "",
};

function priceLabel(priceTry: number): string {
  if (priceTry === 0) return "Ücretsiz";
  return `₺${priceTry.toLocaleString("tr-TR")} / ay`;
}

function periodLabel(period: string): string {
  const [year, month] = period.split("-").map(Number);
  const date = new Date(Date.UTC(year, (month ?? 1) - 1, 1));
  return date.toLocaleDateString("tr-TR", { month: "long", year: "numeric" });
}

export default function BillingPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [info, setInfo] = useState<BillingInfo | null>(null);
  const [details, setDetails] = useState<BillingDetails>(EMPTY_DETAILS);
  const [showDetailsForm, setShowDetailsForm] = useState(false);
  const [checkoutHtml, setCheckoutHtml] = useState<string | null>(null);
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [notice, setNotice] = useState<{ kind: "good" | "bad"; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const checkoutRef = useRef<HTMLDivElement>(null);

  const isAdmin = me?.user.role === "admin";

  const refresh = useCallback(async () => {
    const data = await api.billingInfo();
    setInfo(data);
    if (data.billing_details) setDetails(data.billing_details);
    return data;
  }, []);

  useEffect(() => {
    Promise.all([api.me().then(setMe), refresh()]).catch((e) =>
      setError((e as Error).message),
    );
    const params = new URLSearchParams(window.location.search);
    const checkout = params.get("checkout");
    if (checkout === "success") {
      setNotice({ kind: "good", text: "Ödeme alındı — planınız aktif." });
    } else if (checkout === "failed") {
      setNotice({
        kind: "bad",
        text: "Ödeme tamamlanamadı. Kartınızı kontrol edip yeniden deneyin.",
      });
    }
  }, [refresh]);

  // iyzico's checkout form arrives as a <script> snippet; scripts injected via
  // innerHTML never execute, so they are re-created as real script nodes here.
  useEffect(() => {
    const el = checkoutRef.current;
    if (!checkoutHtml || !el) return;
    el.innerHTML = '<div id="iyzipay-checkout-form" class="responsive"></div>';
    const parsed = document.createElement("div");
    parsed.innerHTML = checkoutHtml;
    parsed.querySelectorAll("script").forEach((original) => {
      const script = document.createElement("script");
      if (original.src) script.src = original.src;
      else script.textContent = original.textContent;
      el.appendChild(script);
    });
  }, [checkoutHtml]);

  async function saveDetails(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.saveBillingDetails(details);
      setShowDetailsForm(false);
      setNotice({ kind: "good", text: "Fatura bilgileri kaydedildi." });
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function choosePlan(planId: string) {
    if (!info) return;
    setError(null);
    setNotice(null);
    if (info.provider_mode === "iyzico" && !info.billing_details) {
      setShowDetailsForm(true);
      setNotice({ kind: "bad", text: "Önce fatura bilgilerinizi kaydedin." });
      return;
    }
    setBusyPlan(planId);
    try {
      const result = await api.checkout(planId);
      if (result.mode === "mock") {
        setNotice({ kind: "good", text: `${result.plan.name} planı aktif edildi.` });
        await refresh();
      } else {
        setCheckoutHtml(result.checkout_html);
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? err.message
          : (err as Error).message,
      );
    } finally {
      setBusyPlan(null);
    }
  }

  async function cancel() {
    if (!confirmCancel) {
      setConfirmCancel(true);
      return;
    }
    setError(null);
    try {
      await api.cancelSubscription();
      setConfirmCancel(false);
      setNotice({
        kind: "good",
        text: "Aboneliğiniz iptal edildi — Deneme planına dönüldü.",
      });
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const pct =
    info && info.usage.quota > 0
      ? Math.min(100, Math.round((info.usage.used / info.usage.quota) * 100))
      : 0;

  return (
    <main>
      <h1 className="page-title">Plan ve kullanım</h1>
      <p className="page-sub">
        Fiyatlandırma aylık işlenen CV hacmine göredir. Aynı CV&apos;nin tekrar taranması
        ücretsizdir — kota yalnızca yeni CV&apos;ler için işler ve her ay başında sıfırlanır.
      </p>

      {notice && <div className={`notice ${notice.kind}`} style={{ marginBottom: 12 }}>{notice.text}</div>}
      {error && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}

      {info && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="hstack" style={{ justifyContent: "space-between" }}>
            <div className="hstack">
              <span className="chip accent">{info.plan.name}</span>
              {info.status !== "trial" && info.status !== "active" && (
                <span className={`chip${info.status === "past_due" ? " bad" : ""}`}>
                  {STATUS_LABEL[info.status] ?? info.status}
                </span>
              )}
              {info.status === "past_due" && (
                <span className="tiny">Ödeme alınamadığı için Deneme kotası uygulanıyor.</span>
              )}
            </div>
            {isAdmin && (info.status === "active" || info.status === "past_due") && (
              <button className="btn btn-ghost" onClick={cancel} type="button">
                {confirmCancel ? "Emin misiniz? İptal et" : "Aboneliği iptal et"}
              </button>
            )}
          </div>
          <div style={{ marginTop: 14 }}>
            <div className="hstack" style={{ justifyContent: "space-between" }}>
              <span className="tiny">{periodLabel(info.usage.period)} kullanımı</span>
              <span className="tiny">
                {info.usage.used.toLocaleString("tr-TR")} /{" "}
                {info.usage.quota.toLocaleString("tr-TR")} CV ·{" "}
                {info.usage.remaining.toLocaleString("tr-TR")} kaldı
              </span>
            </div>
            <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
              <div className={`meter-fill${pct >= 90 ? " hot" : ""}`} style={{ width: `${pct}%` }} />
            </div>
          </div>
        </div>
      )}

      {checkoutHtml && (
        <div className="card" style={{ marginBottom: 12 }}>
          <span className="field-label">Güvenli ödeme (iyzico)</span>
          <div ref={checkoutRef} />
        </div>
      )}

      {info && (
        <div className="plan-grid">
          {info.catalog.map((plan) => {
            const isCurrent = plan.id === info.plan.id;
            return (
              <div key={plan.id} className={`card plan-card${isCurrent ? " current" : ""}`}>
                <div className="hstack" style={{ justifyContent: "space-between" }}>
                  <strong>{plan.name}</strong>
                  {isCurrent && <span className="chip good">Mevcut plan</span>}
                </div>
                <div className="plan-price">{priceLabel(plan.monthly_price_try)}</div>
                <div className="tiny">
                  Ayda {plan.cv_quota_per_month.toLocaleString("tr-TR")} CV
                </div>
                {isAdmin && !isCurrent && plan.monthly_price_try > 0 && (
                  <button
                    className="btn btn-primary"
                    style={{ marginTop: 12 }}
                    disabled={busyPlan !== null}
                    onClick={() => choosePlan(plan.id)}
                    type="button"
                  >
                    {busyPlan === plan.id ? "Hazırlanıyor…" : "Bu plana geç"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {info?.provider_mode === "iyzico" && isAdmin && (
        <div className="card quiet" style={{ marginTop: 12 }}>
          <div className="hstack" style={{ justifyContent: "space-between" }}>
            <span className="field-label" style={{ marginBottom: 0 }}>
              Fatura bilgileri
            </span>
            {!showDetailsForm && (
              <button className="btn btn-ghost" onClick={() => setShowDetailsForm(true)} type="button">
                {info.billing_details ? "Düzenle" : "Ekle"}
              </button>
            )}
          </div>
          {!showDetailsForm && info.billing_details && (
            <p className="tiny" style={{ margin: "8px 0 0" }}>
              {info.billing_details.company_title} · VKN {info.billing_details.tax_number} ·{" "}
              {info.billing_details.city}
            </p>
          )}
          {showDetailsForm && (
            <form className="stack" style={{ marginTop: 12 }} onSubmit={saveDetails}>
              <div className="hstack">
                <input
                  className="input"
                  style={{ flex: 2, minWidth: 220 }}
                  required
                  placeholder="Şirket unvanı"
                  value={details.company_title}
                  onChange={(e) => setDetails({ ...details, company_title: e.target.value })}
                  aria-label="Şirket unvanı"
                />
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 140 }}
                  required
                  minLength={10}
                  maxLength={11}
                  placeholder="Vergi no"
                  value={details.tax_number}
                  onChange={(e) => setDetails({ ...details, tax_number: e.target.value })}
                  aria-label="Vergi numarası"
                />
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 140 }}
                  required
                  placeholder="Vergi dairesi"
                  value={details.tax_office}
                  onChange={(e) => setDetails({ ...details, tax_office: e.target.value })}
                  aria-label="Vergi dairesi"
                />
              </div>
              <div className="hstack">
                <input
                  className="input"
                  style={{ flex: 2, minWidth: 220 }}
                  required
                  placeholder="Adres"
                  value={details.address}
                  onChange={(e) => setDetails({ ...details, address: e.target.value })}
                  aria-label="Adres"
                />
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 140 }}
                  required
                  placeholder="Şehir"
                  value={details.city}
                  onChange={(e) => setDetails({ ...details, city: e.target.value })}
                  aria-label="Şehir"
                />
              </div>
              <div className="hstack">
                <button className="btn btn-primary" type="submit">
                  Kaydet
                </button>
                <button className="btn btn-ghost" type="button" onClick={() => setShowDetailsForm(false)}>
                  Vazgeç
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {!isAdmin && (
        <p className="tiny" style={{ marginTop: 12 }}>
          Plan değişikliklerini yalnızca yöneticiler yapabilir.
        </p>
      )}
    </main>
  );
}
