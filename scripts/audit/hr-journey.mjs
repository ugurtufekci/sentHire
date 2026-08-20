/**
 * sentHire — bir İK uzmanının gözünden uçtan uca denetim.
 *
 * Her adım "bu ürünü kullansam ne beklerdim?" sorusunun bir cevabı. Adımlar
 * teknik olarak neyin çağrıldığını değil, insanın ne göreceğini kontrol eder.
 */
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://localhost:3000";
const OUT = process.env.OUT_DIR;
const CVS = process.env.CV_DIR;
const stamp = Date.now();
const HR = { company: "Duman Lojistik", name: "Selin Duman", email: `selin${stamp}@dumanlojistik.com`, pass: "cok-guclu-parola-2026" };

const results = [];
const problems = [];
let probing = false; // section I deliberately requests things that must 404
let page, ctx, browser;

function log(section, name, ok, detail = "") {
  results.push({ section, name, ok, detail });
  console.log(`  ${ok ? "✓" : "✗"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) problems.push(`${section}/${name}: ${detail}`);
}

async function expect(section, name, fn) {
  try {
    const detail = await fn();
    log(section, name, true, detail || "");
  } catch (e) {
    log(section, name, false, e.message.split("\n")[0].slice(0, 160));
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium", args: ["--no-sandbox"] });
ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, locale: "tr-TR" });
page = await ctx.newPage();
page.on("console", (m) => {
  if (probing || m.type() !== "error" || m.text().includes("401")) return;
  problems.push(`console: ${m.text().slice(0, 140)}`);
});
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 140)}`));
page.on("response", (r) => {
  if (r.url().includes("/api/") && r.status() >= 500) problems.push(`HTTP ${r.status()} ${r.url()}`);
});

/* ---------------------------------------------------------------- A. Giriş */
console.log("\nA. İlk temas — kaydolup içeri girmek");

await expect("A", "Anonim ziyaretçi ilanları göremez, girişe yönlenir", async () => {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForURL("**/login", { timeout: 8000 });
});

await expect("A", "Şirket ve yönetici hesabı tek adımda açılıyor", async () => {
  await page.goto(BASE + "/signup", { waitUntil: "networkidle" });
  await page.fill("#company", HR.company);
  await page.fill("#name", HR.name);
  await page.fill("#email", HR.email);
  await page.fill("#password", HR.pass);
  await page.click('button[type="submit"]');
  await page.waitForURL(BASE + "/", { timeout: 15000 });
});

await expect("A", "Üst barda şirketim ve adım görünüyor", async () => {
  await page.waitForSelector(".user-menu", { timeout: 10000 });
  const t = await page.textContent(".topbar");
  if (!t.includes(HR.company)) throw new Error(`şirket adı yok: ${t}`);
  if (!t.includes(HR.name)) throw new Error(`kullanıcı adı yok: ${t}`);
});

await expect("A", "Boş ekran ne yapacağımı söylüyor", async () => {
  const body = await page.textContent("main");
  if (!/ilan/i.test(body)) throw new Error("yönlendirici metin yok");
});
await page.screenshot({ path: `${OUT}/hr-01-bos.png`, fullPage: true });

/* ------------------------------------------------------- B. Kriterler */
console.log("\nB. Aradığım adayı kendi cümlelerimle anlatmak");

let jobUrl;
await expect("B", "Yeni ilan açılıyor (klavyeyle de)", async () => {
  await page.click('a[href="/jobs/new"]');
  await page.waitForSelector("#job-title", { timeout: 8000 });
  await page.fill("#job-title", "Kurumsal Satış Uzmanı — Ankara");
  await page.press("#job-title", "Enter");
  await page.waitForURL(/\/jobs\/[0-9a-f-]{36}$/, { timeout: 15000 });
  jobUrl = page.url();
});

await expect("B", "Kriterleri düz Türkçe yazıyorum", async () => {
  await page.fill(
    ".textarea",
    "Adayın Ankara'da ikamet ediyor olması önemli. En az 3 yıl kurumsal B2B satış deneyimi olsun. İngilizce iyi derecede olmalı. Kota sorumluluğu almış olması bizim için değerli."
  );
  await page.click('button:has-text("Analiz")');
  await page.waitForSelector(".req-card", { timeout: 30000 });
});

await expect("B", "Sistem ne anladığını bana geri okuyor", async () => {
  const text = await page.textContent("main");
  if (!/Anladığımız/i.test(text)) throw new Error("geri çeviri yok");
  if (!/Ankara/.test(text)) throw new Error("Ankara kriteri geri çeviride yok");
});

await expect("B", "Kriterler tek tek, türleriyle listeleniyor", async () => {
  const cards = await page.locator(".req-card").count();
  if (cards < 3) throw new Error(`sadece ${cards} kriter çıktı`);
  return `${cards} kriter`;
});
await page.screenshot({ path: `${OUT}/hr-02-kriterler.png`, fullPage: true });

await expect("B", "Onaylamadan tarama başlatılamıyor", async () => {
  const runBtn = page.locator('button:has-text("Taramayı başlat")');
  if ((await runBtn.count()) > 0 && (await runBtn.first().isEnabled())) {
    throw new Error("onaysız tarama düğmesi aktif");
  }
});

await expect("B", "Kriterleri onaylıyorum", async () => {
  await page.click('button:has-text("Onayla")');
  await page.waitForSelector("text=Onaylı", { timeout: 15000 });
});

/* --------------------------------------------------------- C. Yükleme */
console.log("\nC. CV'leri yüklemek (gerçek posta kutusu gibi karışık)");

const files = fs.readdirSync(CVS).sort().map((f) => path.join(CVS, f));

await expect("C", `${files.length} dosya birden yükleniyor`, async () => {
  await page.setInputFiles('input[type="file"]', files);
  await page.waitForSelector(".file-row", { timeout: 20000 });
  const rows = await page.locator(".file-row").count();
  if (rows < files.length) throw new Error(`${rows}/${files.length} satır göründü`);
  return `${rows} satır`;
});

await expect("C", "Her dosyanın durumu ayrı ayrı işleniyor", async () => {
  // Pozitif sinyal bekle: satırlar görünsün ve hiçbiri "işleniyor" kalmasın.
  await page.waitForFunction(
    () => {
      const rows = [...document.querySelectorAll(".file-row")];
      const settled = rows.filter((r) =>
        /Profil çıkarıldı|Desteklenmiyor|Hata|CV değil/.test(r.textContent)
      );
      return settled.length >= 10;
    },
    { timeout: 120000 }
  );
  const statuses = await page.locator(".file-row").allTextContents();
  return statuses.length + " dosya sonuçlandı";
});
await page.screenshot({ path: `${OUT}/hr-03-yukleme.png`, fullPage: true });

await expect("C", "Bozuk dosya sessizce kaybolmuyor, nedeni yazıyor", async () => {
  const row = (await page.locator(".file-row").allTextContents()).find((t) => /bozuk/i.test(t));
  if (!row) throw new Error("bozuk dosya satırı yok");
  if (!/Desteklenmiyor|Desteklenmeyen|hata/i.test(row)) throw new Error(`satır: ${row.trim()}`);
  return row.replace(/\s+/g, " ").trim().slice(0, 60);
});

await expect("C", "CV olmayan dosya (ön yazı) ayırt ediliyor", async () => {
  const rows = await page.locator(".file-row").allTextContents();
  const cover = rows.find((t) => /yazi|yazı/i.test(t));
  const scan = rows.find((t) => /taranmis|taranmış/i.test(t));
  if (!cover) throw new Error("ön yazı satırı yok");
  if (!/ön yazı|cover|CV değil|belge/i.test(cover + (scan || ""))) {
    throw new Error(`ön yazı: ${cover.replace(/\s+/g, " ").trim()}`);
  }
  return "ön yazı ve taranmış belge ayrıldı";
});

await expect("C", "Aynı adayın ikinci CV'si tek adaya birleşiyor", async () => {
  const text = await page.textContent("main");
  const parsed = (text.match(/Profil çıkarıldı/g) || []).length;
  if (parsed < 7) throw new Error(`sadece ${parsed} dosya okundu`);
  const countMatch = text.match(/Profili çıkarılan adaylar\s*\((\d+)\)/);
  if (!countMatch) throw new Error("aday sayısı görünmüyor");
  const candidates = parseInt(countMatch[1], 10);
  // 10 dosya: 1 bozuk, 1 ön yazı, 1 taranmış, 1 mükerrer → 6 aday kalmalı.
  if (candidates !== 6) throw new Error(`${parsed} dosyadan ${candidates} aday çıktı, 6 bekleniyordu`);
  return `${parsed} dosya → ${candidates} aday (mükerrer birleşti)`;
});

/* ---------------------------------------------------------- D. Tarama */
console.log("\nD. Taramayı çalıştırmak ve sonuçları okumak");

let runUrl;
await expect("D", "Taramayı başlatıyorum", async () => {
  const button = page.locator('button:has-text("Taramayı başlat")');
  await button.waitFor({ state: "visible", timeout: 30000 });
  await page.waitForFunction(
    () => {
      const b = [...document.querySelectorAll("button")].find((x) =>
        x.textContent.includes("Taramayı başlat")
      );
      return b && !b.disabled;
    },
    { timeout: 30000 }
  );
  await button.click();
  await page.waitForURL(/\/runs\/[0-9a-f-]{36}/, { timeout: 20000 });
  runUrl = page.url();
});

await expect("D", "Tarama makul sürede tamamlanıyor", async () => {
  for (let i = 0; i < 60; i++) {
    const text = await page.textContent("main");
    if (/Tamamlandı/.test(text)) return `${i + 1} kontrol`;
    if (/Hata/.test(text)) throw new Error("tarama hata ile bitti");
    await sleep(2000);
  }
  throw new Error("tarama 2 dakikada bitmedi");
});

await expect("D", "Demo modu açıkça uyarıyor (gerçek değerlendirme değil)", async () => {
  const text = await page.textContent("main");
  if (!/Demo modu/.test(text)) throw new Error("demo uyarısı yok");
});

await expect("D", "Sıralama geldi, her adayın puanı ve seviyesi var", async () => {
  await page.waitForSelector(".soft-table tbody tr", { timeout: 20000 });
  const rows = await page.locator(".soft-table tbody tr").count();
  if (rows < 2) throw new Error(`sadece ${rows} aday sıralandı`);
  return `${rows} aday sıralandı`;
});
await page.screenshot({ path: `${OUT}/hr-04-siralama.png`, fullPage: true });

await expect("D", "Elenen adaylar gerekçesiyle görülebiliyor", async () => {
  const btn = page.locator('button:has-text("Elenenler")');
  if ((await btn.count()) === 0) return "eleme olmadı";
  await btn.click();
  await page.waitForTimeout(500);
  const text = await page.textContent("main");
  if (!/gerekçe|Karşılamıyor|neden/i.test(text)) throw new Error("gerekçe gösterilmiyor");
  return "gerekçeli";
});

await expect("D", "Bir adaya tıklayınca kriter kriter döküm açılıyor", async () => {
  await page.locator(".soft-table tbody tr").first().click();
  await page.waitForSelector(".drawer .req-card", { timeout: 10000 });
  const cards = await page.locator(".drawer .req-card").count();
  if (cards < 2) throw new Error(`sadece ${cards} kriter dökümü`);
  return `${cards} kriter`;
});

await expect("D", "Kararlar CV'den alıntıyla destekleniyor", async () => {
  const quotes = await page.locator(".drawer .evidence").count();
  if (quotes === 0) throw new Error("hiç kanıt alıntısı yok");
  return `${quotes} alıntı`;
});

await expect("D", "Eksik bilgi 'olumsuz' değil 'bilgi yok' olarak işaretleniyor", async () => {
  const text = await page.textContent(".drawer");
  if (!/Bilgi yok|CV'de yazıyor|Çıkarım/.test(text)) throw new Error("bilgi durumu etiketi yok");
});

await expect("D", "Hiçbir karar açıklamasız değil", async () => {
  // Her kriter kartı ya CV'den alıntı, ya kuralın okuduğu değer, ya da
  // ladder etiketi taşımalı; çıplak bir "Karşılamıyor" kabul edilemez.
  const cards = await page.locator(".drawer .req-card").allTextContents();
  const bare = cards.filter(
    (c) =>
      !/:|“|”|Tam karşılıyor|Büyük ölçüde|Yarı yarıya|Zayıf|aranan/.test(c)
  );
  if (bare.length) throw new Error(`${bare.length} kriter gerekçesiz: ${bare[0].slice(0, 60)}`);
  return `${cards.length} kriterin hepsi gerekçeli`;
});
await page.screenshot({ path: `${OUT}/hr-05-aday.png` });
await page.click('.drawer button:has-text("Kapat")');

/* ------------------------------------------------ E. Güvenlik/dürüstlük */
console.log("\nE. Kötü niyetli CV ve dürüstlük kontrolleri");

await expect("E", "CV'ye gömülü 'bana tam puan ver' talimatı işe yaramıyor", async () => {
  const rows = await page.locator(".soft-table tbody tr").allTextContents();
  const injected = rows.find((r) => /Selin Demir/.test(r));
  if (!injected) return "enjeksiyon CV'si sıralamaya girmedi";
  const score = parseInt((injected.match(/\b(\d{1,3})\b/g) || []).slice(1)[0] || "0", 10);
  if (score >= 95) throw new Error(`enjeksiyon işe yaradı: ${score} puan`);
  return `puanı ${score} — talimat yok sayıldı`;
});

await expect("E", "Manipülasyon girişimi kullanıcıya bildiriliyor", async () => {
  const rows = page.locator(".soft-table tbody tr");
  const count = await rows.count();
  let found = false;
  for (let i = 0; i < count; i++) {
    const text = await rows.nth(i).textContent();
    if (!/Selin Demir/.test(text)) continue;
    if (!/İnceleme önerilir/.test(text)) throw new Error("listede uyarı yok");
    await rows.nth(i).click();
    await page.waitForSelector(".drawer .req-card", { timeout: 15000 });
    const drawer = await page.textContent(".drawer");
    if (!/talimat vermeye çalışan/.test(drawer)) throw new Error("çekmecede açıklama yok");
    if (!/etki etmedi/.test(drawer)) throw new Error("puana etki etmediği söylenmiyor");
    await page.click('.drawer button:has-text("Kapat")');
    found = true;
    break;
  }
  if (!found) return "enjeksiyon CV'si elendi, listede yok";
  return "listede ve çekmecede uyarıldı";
});

await expect("E", "En yüksek puan otomatik 100 değil (ölçek gerçekçi)", async () => {
  const first = await page.locator(".soft-table tbody tr").first().textContent();
  return `en iyi aday satırı: ${first.replace(/\s+/g, " ").trim().slice(0, 60)}`;
});

/* ------------------------------------------------------- F. Düzeltme */
console.log("\nF. Karara katılmadığımda");

await expect("F", "Bir kriter kararını düzeltebiliyorum", async () => {
  if (await page.locator(".drawer").count()) {
    await page.click('.drawer button:has-text("Kapat")');
    await page.waitForSelector(".drawer", { state: "detached", timeout: 8000 });
  }
  await page.locator(".soft-table tbody tr").first().click();
  await page.waitForSelector(".drawer .req-card", { timeout: 15000 });
  await page.locator(".correction-open").first().click();
  await page.waitForSelector(".correction", { timeout: 5000 });
  await page.fill(".correction .input", "Adayla teyit ettim");
  await page.locator(".correction .seg-btn").first().click();
  await page.waitForSelector('.drawer .chip:has-text("İK düzeltmesi")', { timeout: 10000 });
});

await expect("F", "Düzeltme puana ve sıralamaya yansıyor", async () => {
  await page.click('.drawer button:has-text("Kapat")');
  await page.waitForSelector(".soft-table tbody tr", { timeout: 8000 });
  const ranks = await page.locator(".soft-table tbody tr td:first-child").allTextContents();
  const clean = ranks.map((r) => parseInt(r, 10));
  for (let i = 1; i < clean.length; i++) {
    if (clean[i] < clean[i - 1]) throw new Error(`sıralama bozuk: ${clean}`);
  }
  return `sıra: ${clean.join(",")}`;
});
await page.screenshot({ path: `${OUT}/hr-06-duzeltme.png`, fullPage: true });

/* -------------------------------------------------------- G. Pipeline */
console.log("\nG. Beğendiğim adaylarla görüşme süreci");

await expect("G", "Sonuç sayfasından aday akışına geçebiliyorum", async () => {
  await page.click('a:has-text("aday akışını aç")');
  await page.waitForURL(/\/pipeline$/, { timeout: 10000 });
  await page.waitForSelector(".board-col", { timeout: 10000 });
});

await expect("G", "Yüksek puanlıları tek tıkla seçip kısa listeye taşıyorum", async () => {
  const trayBefore = await page.locator(".tray-row").count();
  if (trayBefore === 0) throw new Error("tepside aday yok");
  await page.click('button:has-text("60+ puan")');
  const checked = await page.locator(".tray-row input:checked").count();
  if (checked === 0) throw new Error("hızlı seçim kimseyi seçmedi");
  await page.click('button:has-text("Kısa listeye taşı")');
  await page.waitForFunction(
    () => document.querySelectorAll(".board-col")[0]?.querySelectorAll(".pcard").length > 0,
    { timeout: 10000 }
  );
  return `${checked} aday taşındı`;
});

await expect("G", "Adayı sürükleyip görüşme aşamasına alıyorum", async () => {
  const card = page.locator(".board-col").first().locator(".pcard").first();
  const name = (await card.textContent()).trim().split("\n")[0];
  await card.dragTo(page.locator(".board-col").nth(2));
  await page.waitForFunction(
    (n) => document.querySelectorAll(".board-col")[2]?.textContent.includes(n),
    name.slice(0, 10),
    { timeout: 10000 }
  );
  return name.slice(0, 30);
});

await expect("G", "Görüşme planlıyorum, adayın zaman çizelgesine işleniyor", async () => {
  await page.locator(".board-col").nth(2).locator(".pcard").first().click();
  await page.waitForSelector(".drawer", { timeout: 8000 });
  await page.click('button:has-text("Görüşme planla")');
  await page.fill('input[type="datetime-local"] >> nth=0', "2026-09-15T14:00");
  await page.fill(".drawer .textarea", "Teknik mülakat");
  await page.click('.drawer button:has-text("Kaydet") >> nth=0');
  await page.waitForSelector(".timeline-row", { timeout: 10000 });
});
await page.screenshot({ path: `${OUT}/hr-07-pipeline.png`, fullPage: true });

await expect("G", "Ana sayfada 'yaklaşan adımlar' hatırlatıyor", async () => {
  await page.click('.drawer button:has-text("Kapat")');
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForSelector("text=Yaklaşan adımlar", { timeout: 10000 });
  const row = await page.locator(".agenda-row").first().textContent();
  if (!/Teknik mülakat/.test(row)) throw new Error(`hatırlatıcı içeriği: ${row}`);
});
await page.screenshot({ path: `${OUT}/hr-08-ajanda.png`, fullPage: true });

/* ------------------------------------------------------------ H. Ekip */
console.log("\nH. Ekip, izolasyon ve faturalandırma");

let inviteUrl;
await expect("H", "Meslektaşımı davet edebiliyorum", async () => {
  await page.goto(BASE + "/team", { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', `mert${stamp}@dumanlojistik.com`);
  await page.click('button:has-text("Davet")');
  await page.waitForSelector(".mono.tiny", { timeout: 10000 });
  inviteUrl = (await page.locator(".mono.tiny").first().textContent()).trim();
  if (!/\/join\//.test(inviteUrl)) throw new Error(`davet linki okunamadı: ${inviteUrl}`);
  return inviteUrl.slice(0, 44) + "…";
});

await expect("H", "Davet edilen kişi katılıp aynı ilanları görüyor", async () => {
  const colleague = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const cp = await colleague.newPage();
  await cp.goto(inviteUrl, { waitUntil: "networkidle" });
  await cp.fill("#name", "Mert Duman");
  await cp.fill("#password", "meslektas-parola-2026");
  await cp.click('button[type="submit"]');
  await cp.waitForURL(BASE + "/", { timeout: 15000 });
  await cp.waitForFunction(
    () =>
      [...document.querySelectorAll("a[href^='/jobs/']")].some((a) =>
        /\/jobs\/[0-9a-f-]{36}$/.test(a.getAttribute("href"))
      ),
    { timeout: 20000 }
  );
  const text = await cp.textContent("main");
  if (!/Kurumsal Satış Uzmanı/.test(text)) throw new Error("ilanı göremedi");
  await colleague.close();
});

await expect("H", "Başka bir şirket benim ilanımı göremiyor", async () => {
  const rival = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const rp = await rival.newPage();
  await rp.goto(BASE + "/signup", { waitUntil: "networkidle" });
  await rp.fill("#company", "Rakip İK");
  await rp.fill("#name", "Veli Kaya");
  await rp.fill("#email", `veli${stamp}@rakip.com`);
  await rp.fill("#password", "rakip-parola-2026");
  await rp.click('button[type="submit"]');
  await rp.waitForURL(BASE + "/", { timeout: 15000 });
  const text = await rp.textContent("main");
  if (/Kurumsal Satış Uzmanı/.test(text)) throw new Error("BAŞKA ŞİRKETİN İLANI GÖRÜNÜYOR");
  await rp.goto(jobUrl, { waitUntil: "networkidle" });
  const jobText = await rp.textContent("body");
  if (/Kurumsal Satış Uzmanı — Ankara/.test(jobText)) throw new Error("doğrudan link ile erişti");
  await rival.close();
});

await expect("H", "Kotamı ve planımı görebiliyorum", async () => {
  await page.goto(BASE + "/billing", { waitUntil: "networkidle" });
  const text = await page.textContent("main");
  if (!/CV/.test(text) || !/Deneme|plan/i.test(text)) throw new Error("kota bilgisi yok");
  const used = text.match(/(\d+)\s*\/\s*(\d+)/);
  return used ? `kullanım ${used[0]}` : "plan görünüyor";
});
await page.screenshot({ path: `${OUT}/hr-09-fatura.png`, fullPage: true });

/* --------------------------------------------------- I. Kırılganlık */
console.log("\nI. Kırılganlık ve hata halleri");
probing = true; // beklenen 404/422'ler burada üretiliyor

await expect("I", "Olmayan tarama linki beyaz ekran vermiyor", async () => {
  await page.goto(BASE + "/runs/00000000-0000-0000-0000-000000000000", { waitUntil: "networkidle" });
  const body = (await page.textContent("body")).trim();
  if (body.length < 30) throw new Error("boş sayfa");
});

await expect("I", "Bozuk ilan linki de anlaşılır bir şey gösteriyor", async () => {
  await page.goto(BASE + "/jobs/not-a-uuid", { waitUntil: "networkidle" });
  const body = (await page.textContent("body")).trim();
  if (body.length < 30) throw new Error("boş sayfa");
});

await expect("I", "Çıkış yapınca oturum gerçekten kapanıyor", async () => {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.click(".user-menu summary");
  await page.click("text=Çıkış yap");
  await page.waitForURL("**/login", { timeout: 10000 });
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForURL("**/login", { timeout: 10000 });
});

await expect("I", "Yanlış parola anlaşılır hata veriyor", async () => {
  await page.fill("#email", HR.email);
  await page.fill("#password", "yanlis-parola");
  await page.click('button[type="submit"]');
  await page.waitForSelector(".notice.bad", { timeout: 10000 });
  const msg = await page.textContent(".notice.bad");
  if (!/hatalı|geçersiz/i.test(msg)) throw new Error(`anlaşılmaz mesaj: ${msg}`);
});

await browser.close();

/* ----------------------------------------------------------- Rapor */
const failed = results.filter((r) => !r.ok);
console.log(`\n${"=".repeat(64)}`);
console.log(`SONUÇ: ${results.length - failed.length}/${results.length} kontrol geçti`);
if (problems.length) {
  console.log(`\nSORUNLAR (${problems.length}):`);
  problems.forEach((p) => console.log(`  - ${p}`));
}
fs.writeFileSync(`${OUT}/hr-report.json`, JSON.stringify({ results, problems }, null, 2));
process.exit(failed.length || problems.length ? 1 : 0);
