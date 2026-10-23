import { test, expect, type Page } from "@playwright/test";
import { waitForRun } from "./fixtures";

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
