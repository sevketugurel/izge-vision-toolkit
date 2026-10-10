import { test, expect } from "@playwright/test";
import { post, syntheticVideo } from "./fixtures";

test("kayıt yükleme → iş tamamlanır → sonuç ekranı", async ({ page, request }) => {
  const farm = await post(request, "/api/v1/farms", { name: `E2E ${Date.now()}` });
  const base = `/api/v1/farms/${farm.id}`;
  const barn = await post(request, `${base}/barns`, { name: "Ahır" });
  await post(request, `${base}/cameras`, { name: "E2E Kamera", barn_id: barn.id });

  await page.goto("/");
  await page.getByLabel("İşletme").selectOption(farm.id);
  await page.getByRole("button", { name: "Video ve işler" }).click();
  await page.locator('select[name="camera_id"]').selectOption({ label: "E2E Kamera" });
  await page.locator('input[type="file"]').setInputFiles(syntheticVideo("e2e-record.mp4"));
  await page.getByRole("button", { name: "Yükle ve doğrula" }).click();

  const card = page.locator(".media-card", { hasText: "e2e-record.mp4" });
  await expect(card).toBeVisible({ timeout: 60_000 });
  await card.click();
  await page.getByRole("button", { name: "Analizi başlat" }).click();

  const job = page.locator("table tbody tr").first();
  await expect(job).toContainText("Tamamlandı", { timeout: 90_000 });

  const runs = await (await request.get(`${base}/runs`)).json();
  expect(runs).toHaveLength(1);
  expect(runs[0].status).toBe("succeeded");

  await page.getByRole("button", { name: "İşletme özeti" }).click();
  await expect(page.getByRole("heading", { name: "Günlük görünüm" })).toBeVisible();
  // No trained model is connected: the result must be explicit, never invented.
  await expect(page.locator("header .badge")).toContainText("Model bağlı değil");
});
