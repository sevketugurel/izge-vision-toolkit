import { test, expect, type Page } from "@playwright/test";
import { post, stallVideo, waitForRun } from "./fixtures";

// Records the highest number of simultaneously running transform animations, sampled every frame,
// so short enter animations cannot slip between two measurements.
const OBSERVER = () => {
  const w = window as any;
  w.__maxTransformAnimations = 0;
  const sample = () => {
    const n = document
      .getAnimations()
      .filter((a) =>
        (a.effect as KeyframeEffect | null)
          ?.getKeyframes()
          .some((k) => "transform" in k || "translate" in k || "scale" in k),
      ).length;
    w.__maxTransformAnimations = Math.max(w.__maxTransformAnimations, n);
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
};

async function openFeedingWithRows(page: Page, request: any, reducedMotion: "reduce" | "no-preference") {
  const farms = await (await request.get("/api/v1/farms")).json();
  const demo = farms.find((f: any) => f.is_demo);
  test.skip(!demo, "Demo işletmesi yok: önce python -m izge.demo");
  const base = `/api/v1/farms/${demo.id}`;
  const runs = await (await request.get(`${base}/runs`)).json();
  const run = runs.find((r: any) => r.analyzer === "feeding-demo-1");
  await waitForRun(request, base, run.id, 300_000);
  const day = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Istanbul" }).format(
    new Date(run.context.recorded_at),
  );

  await page.emulateMedia({ reducedMotion });
  await page.addInitScript(OBSERVER);
  await page.goto("/");
  await page.getByLabel("İşletme").selectOption(demo.id);
  await page.getByLabel("Rapor günü").fill(day);
  await page.getByRole("button", { name: "Yemde süresi" }).click();
  await page.getByLabel("Yemde kamerası").selectOption(run.context.camera_id);
  const rows = page.getByRole("table", { name: "Günlük yemde özeti" }).locator("tbody tr");
  await expect(rows).toHaveCount(4);
  // Exercise a state transition too: switch the day away and back so rows re-enter.
  await page.getByLabel("Rapor günü").fill("2000-01-01");
  await page.getByLabel("Rapor günü").fill(day);
  await expect(rows).toHaveCount(4);
  // Let any enter animation (200 ms + stagger) run its course while the observer samples.
  await page.waitForTimeout(1_000);
  await page.getByRole("button", { name: "Etiketleme" }).click();
  await expect(page.getByLabel("Etiketleyen")).toBeVisible();
  await page.waitForTimeout(1_000);
  return page.evaluate(() => (window as any).__maxTransformAnimations as number);
}

test("reduced-motion: besleme geçişlerinde transform animasyonu 0", async ({ page, request }) => {
  expect(await openFeedingWithRows(page, request, "reduce")).toBe(0);
});

test("normal hareket: aynı senaryo transform animasyonu üretir (test geçerliliği)", async ({ page, request }) => {
  expect(await openFeedingWithRows(page, request, "no-preference")).toBeGreaterThan(0);
});

// Form and card transitions: RecordedAtField hint, label-stage card, error and last-action messages.
async function runLabelingTransitions(page: Page, request: any, reducedMotion: "reduce" | "no-preference") {
  const farm = await post(request, "/api/v1/farms", { name: `Hareket E2E ${Date.now()}` });
  const base = `/api/v1/farms/${farm.id}`;
  const barn = await post(request, `${base}/barns`, { name: "Ahır" });
  const camera = await post(request, `${base}/cameras`, {
    name: "Sentetik", barn_id: barn.id, config_key: "sentetik_test",
  });

  await page.emulateMedia({ reducedMotion });
  await page.addInitScript(OBSERVER);
  await page.goto("/");
  await page.getByLabel("İşletme").selectOption(farm.id);
  await page.getByRole("button", { name: "Video ve işler" }).click();
  await page.locator('select[name="camera_id"]').selectOption(camera.id);
  // Hint changes from "Dosya seçin" to the parsed-from-filename text.
  await expect(page.locator("#recorded-at-hint")).toContainText("Dosya seçin");
  await page.locator('input[type="file"]').setInputFiles(stallVideo());
  await expect(page.locator("#recorded-at-hint")).toContainText("Dosya adından okundu");
  await page.getByRole("button", { name: "Yükle ve doğrula" }).click();
  const card = page.locator(".media-card", { hasText: "SENTETIK_20261010_143000.mp4" });
  await expect(card).toBeVisible({ timeout: 60_000 });
  await card.click();
  await page.getByRole("button", { name: "Etiketleme örnekleri oluştur" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Kaydedildi" })).toBeVisible();
  const runs = await (await request.get(`${base}/runs`)).json();
  await waitForRun(request, base, runs.find((r: any) => r.analyzer === "crops-1").id);

  await page.locator("nav").getByRole("button", { name: "Etiketleme" }).click();
  await expect(page.locator(".label-stage")).toBeVisible();
  // Error path: no labeler name -> alert enters; then fix it and label (card swap + last-action message).
  await page.getByLabel("Etiketleyen").fill("");
  await page.locator(".label-crop").click();
  await page.keyboard.press("1");
  await expect(page.getByRole("alert")).toContainText("Etiketleyen adını girin");
  await page.getByLabel("Etiketleyen").fill("e2e-hareket");
  const first = await page.locator(".label-stage").getAttribute("data-sample");
  await page.locator(".label-crop").click();
  await page.keyboard.press("1");
  await expect(page.getByLabel("Son işlem")).toContainText("Yemde");
  await expect(page.locator(".label-stage")).not.toHaveAttribute("data-sample", first!);
  await page.keyboard.press("z");
  await expect(page.getByLabel("Son işlem")).toContainText("Geri alındı");
  await page.waitForTimeout(1_000);
  return page.evaluate(() => (window as any).__maxTransformAnimations as number);
}

test("reduced-motion: form, kart, hata ve son işlem geçişlerinde transform animasyonu 0", async ({ page, request }) => {
  expect(await runLabelingTransitions(page, request, "reduce")).toBe(0);
});

test("normal hareket: form ve kart geçişleri transform animasyonu üretir (test geçerliliği)", async ({ page, request }) => {
  expect(await runLabelingTransitions(page, request, "no-preference")).toBeGreaterThan(0);
});
