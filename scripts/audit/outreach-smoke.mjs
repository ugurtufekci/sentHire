import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://localhost:3000";
const OUT = process.env.OUT_DIR;
const CVS = process.env.CV_DIR;
const stamp = Date.now();
const problems = [];
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium", args: ["--no-sandbox"] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1050 }, locale: "tr-TR" });
const page = await ctx.newPage();
page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("401")) problems.push(`console: ${m.text().slice(0,120)}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0,120)}`));
page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) problems.push(`HTTP ${r.status()} ${r.url()}`); });

async function step(name, fn) {
  try { const d = await fn(); console.log(`  ✓ ${name}${d ? ` — ${d}` : ""}`); }
  catch (e) { console.log(`  ✗ ${name} — ${e.message.split("\n")[0].slice(0,140)}`); problems.push(name); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// setup: workspace, job, criteria, CVs, run
await page.goto(BASE + "/signup", { waitUntil: "networkidle" });
await page.fill("#company", "Duman Lojistik");
await page.fill("#name", "Selin Duman");
await page.fill("#email", `selin${stamp}@dumanlojistik.com`);
await page.fill("#password", "cok-guclu-parola-2026");
await page.click('button[type="submit"]');
await page.waitForURL(BASE + "/", { timeout: 15000 });
await page.goto(BASE + "/jobs/new", { waitUntil: "networkidle" });
await page.fill("#job-title", "Kurumsal Satış Uzmanı — Ankara");
await page.press("#job-title", "Enter");
await page.waitForURL(/\/jobs\/[0-9a-f-]{36}$/, { timeout: 15000 });
const jobUrl = page.url();
await page.fill(".textarea", "Ankara'da ikamet etsin. En az 3 yıl deneyim olsun. İngilizce iyi derecede olmalı.");
await page.click('button:has-text("Analiz")');
await page.waitForSelector(".req-card", { timeout: 30000 });
await page.click('button:has-text("Onayla")');
await page.waitForSelector("text=Onaylı", { timeout: 15000 });
await page.setInputFiles('input[type="file"]', fs.readdirSync(CVS).sort().map((f) => path.join(CVS, f)));
await page.waitForFunction(
  () => [...document.querySelectorAll(".file-row")].filter((r) => /Profil çıkarıldı|Desteklenmiyor/.test(r.textContent)).length >= 10,
  { timeout: 120000 }
);
await page.click('button:has-text("Taramayı başlat")');
await page.waitForURL(/\/runs\//, { timeout: 20000 });
for (let i = 0; i < 40; i++) {
  if (/Tamamlandı/.test(await page.textContent("main"))) break;
  await sleep(2000);
}
console.log("\nAday akışı ve mesaj gönderimi\n");

await step("panoya geç ve iyi adayları kısa listeye al", async () => {
  await page.goto(jobUrl + "/pipeline", { waitUntil: "networkidle" });
  await page.waitForSelector(".tray-row", { timeout: 15000 });
  await page.click('button:has-text("60+ puan")');
  await page.click('button:has-text("Kısa listeye taşı")');
  await page.waitForFunction(
    () => document.querySelectorAll(".board-col")[0]?.querySelectorAll(".pcard").length > 0,
    { timeout: 15000 }
  );
});

await step("sütun başlığından toplu mesaj panelini aç", async () => {
  await page.locator(".board-col").first().locator(".col-mail").click();
  await page.waitForSelector(".drawer .letter", { timeout: 15000 });
});

await step("varsayılan şablon mülakat daveti ve alanlar dolu geliyor", async () => {
  const letter = await page.textContent(".letter");
  if (/\{\{/.test(letter)) throw new Error("değişkenler doldurulmamış");
  if (!/Duman Lojistik/.test(letter)) throw new Error("şirket adı yok");
  if (!/Kurumsal Satış Uzmanı/.test(letter)) throw new Error("ilan adı yok");
  return letter.replace(/\s+/g, " ").slice(0, 70) + "…";
});

await step("görüşme zamanı yazınca mektuba işleniyor", async () => {
  await page.fill("#msg-when", "25.08.2026 14:00");
  await page.waitForFunction(
    () => document.querySelector(".letter-body")?.textContent.includes("25.08.2026 14:00"),
    { timeout: 10000 }
  );
});

await step("takvim daveti ekleneceği söyleniyor", async () => {
  const hint = await page.textContent(".drawer");
  if (!/takvim daveti eklenecek/.test(hint)) throw new Error("takvim ipucu yok");
});

await step("alıcı listesi kimlere gideceğini gösteriyor", async () => {
  const summary = await page.textContent(".drawer");
  const m = summary.match(/(\d+) kişiye gidecek/);
  if (!m) throw new Error("alıcı özeti yok");
  return `${m[1]} kişi`;
});
await page.screenshot({ path: `${OUT}/mail-1-taslak.png`, fullPage: false });

await step("gönder ve sonucu gör", async () => {
  await page.click('.drawer button:has-text("gönder"), .drawer button:has-text("Gönder")');
  await page.waitForSelector(".notice.accent", { timeout: 20000 });
  const notice = await page.textContent(".notice.accent");
  if (!/gönderildi/.test(notice)) throw new Error(notice);
  if (!/takvim davetiyle/.test(notice)) throw new Error("takvim eki onayı yok: " + notice);
  return notice.replace(/\s+/g, " ").trim().slice(0, 70);
});
await page.screenshot({ path: `${OUT}/mail-2-gonderildi.png`, fullPage: false });

await step("adaylar 'Temas kuruldu' aşamasına taşındı", async () => {
  await page.click('.drawer button:has-text("Kapat")');
  await page.waitForFunction(
    () => document.querySelectorAll(".board-col")[1]?.querySelectorAll(".pcard").length > 0,
    { timeout: 15000 }
  );
});

await step("adayın kartında gönderilen mesaj görünüyor", async () => {
  await page.locator(".board-col").nth(1).locator(".pcard").first().click();
  await page.waitForSelector('.drawer:has-text("Gönderilen mesajlar")', { timeout: 15000 });
  const text = await page.textContent(".drawer");
  if (!/görüşme daveti/.test(text)) throw new Error("mesaj konusu görünmüyor");
  if (!/gönderildi/.test(text)) throw new Error("durum rozeti yok");
});
await page.screenshot({ path: `${OUT}/mail-3-kayit.png`, fullPage: false });

await step("aynı şablonu ikinci kez göndermek onay istiyor", async () => {
  const drawerText = await page.textContent(".drawer");
  if (!/Mesaj yaz/.test(drawerText)) throw new Error("mesaj yaz düğmesi yok");
  await page.click('.drawer button:has-text("Mesaj yaz")');
  await page.waitForSelector(".drawer .letter", { timeout: 15000 });
  await page.click('.drawer button:has-text("Gönder")');
  await page.waitForSelector(".notice.warn", { timeout: 15000 });
  const warn = await page.textContent(".notice.warn");
  if (!/zaten gönderildi/.test(warn)) throw new Error(warn);
  const confirm = await page.locator('button:has-text("Yine de tekrar gönder")').count();
  if (confirm !== 1) throw new Error("onay düğmesi yok");
  return "uyardı ve onay istedi";
});
await page.screenshot({ path: `${OUT}/mail-4-tekrar.png`, fullPage: false });

await step("sıralama CSV'si Türkçe Excel biçiminde iniyor", async () => {
  const runUrl = page.url().includes("/runs/") ? page.url() : null;
  const runsResp = await page.request.get(BASE + "/api/v1/jobs/" + jobUrl.split("/").pop() + "/runs");
  const runId = (await runsResp.json())[0].run_id;
  const resp = await page.request.get(BASE + `/api/v1/runs/${runId}/results.csv`);
  if (resp.status() !== 200) throw new Error(`HTTP ${resp.status()}`);
  const text = await resp.text();
  if (!text.startsWith("\ufeff")) throw new Error("BOM yok — Türkçe Excel bozar");
  if (!text.includes(";Puan;")) throw new Error("noktalı virgül ayracı yok");
  const disposition = resp.headers()["content-disposition"] || "";
  if (!/siralama-/.test(decodeURIComponent(disposition))) throw new Error(`dosya adı: ${disposition}`);
  return decodeURIComponent(disposition).match(/filename\*=UTF-8''([^;]+)/)?.[1] ?? "ok";
});

await step("aday akışı CSV'si sorumlu ve sonraki adımı taşıyor", async () => {
  const resp = await page.request.get(BASE + "/api/v1/jobs/" + jobUrl.split("/").pop() + "/pipeline.csv");
  if (resp.status() !== 200) throw new Error(`HTTP ${resp.status()}`);
  const text = await resp.text();
  if (!/Aday;E-posta;Puan;Aşama/.test(text)) throw new Error("başlık satırı beklenen değil");
  if (!/Temas kuruldu/.test(text)) throw new Error("aşama Türkçe değil");
  return text.split("\n").length - 2 + " satır";
});

await browser.close();
console.log(problems.length ? `\nSORUNLAR (${problems.length}):\n- ` + problems.join("\n- ") : "\nSorun yok.");
process.exit(problems.length ? 1 : 0);
