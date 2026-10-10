import { test, expect } from "@playwright/test";
import { waitForRun } from "./fixtures";

// Requires `python -m izge.demo` (scripts/verify.sh seeds it). Demo results must be labeled DEMO.
test("DEMO yemde sonuçları: zaman çizelgesi, günlük özet, CSV", async ({ page, request }) => {
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

  await page.goto("/");
  await page.getByLabel("İşletme").selectOption(demo.id);
  await page.getByLabel("Rapor günü").fill(day);
  await page.getByRole("button", { name: "Yemde süresi" }).click();
  await page.getByLabel("Yemde kamerası").selectOption(run.context.camera_id);
  await expect(page.locator(".demo-badge")).toHaveText("DEMO");
  await expect(page.getByText("SENTETİK DEMO analizi")).toBeVisible();
  await expect(page.locator(".feeding-timeline rect.state-yemde").first()).toBeVisible();
  const table = page.getByRole("table", { name: "Günlük yemde özeti" });
  await expect(table.locator("tbody tr")).toHaveCount(4);
  const href = await page.getByRole("link", { name: "CSV indir (bu gün) ↓" }).getAttribute("href");
  const csv = await (await request.get(href!)).text();
  expect(csv).toContain("DEMO");
  expect(csv.split("\n")[0]).toContain("yemde_dk");
});
