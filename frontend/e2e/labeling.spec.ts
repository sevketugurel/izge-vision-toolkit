import { test, expect } from "@playwright/test";
import { post, stallVideo, waitForRun } from "./fixtures";

test("etiketleme ekranı: örnek oluşturma, 1/2/3, Z geri al, S atla", async ({ page, request }) => {
  const farm = await post(request, "/api/v1/farms", { name: `Etiket E2E ${Date.now()}` });
  const base = `/api/v1/farms/${farm.id}`;
  const barn = await post(request, `${base}/barns`, { name: "Ahır" });
  const camera = await post(request, `${base}/cameras`, {
    name: "Sentetik", barn_id: barn.id, config_key: "sentetik_test",
  });

  await page.goto("/");
  await page.getByLabel("İşletme").selectOption(farm.id);
  await page.getByRole("button", { name: "Video ve işler" }).click();
  await page.locator('select[name="camera_id"]').selectOption(camera.id);
  await page.locator('input[type="file"]').setInputFiles(stallVideo());
  // The recording start comes from the filename via the camera config regex.
  await expect(page.locator("#recorded-at-hint")).toContainText("Dosya adından okundu");
  await page.getByRole("button", { name: "Yükle ve doğrula" }).click();
  const card = page.locator(".media-card", { hasText: "SENTETIK_20261010_143000.mp4" });
  await expect(card).toBeVisible({ timeout: 60_000 });
  await card.click();

  // No trained model: the feeding analysis is refused with an explicit message.
  await page.getByRole("button", { name: "Yemde analizini başlat" }).click();
  await expect(page.getByRole("alert")).toContainText("Eğitilmiş model yok");

  await page.getByRole("button", { name: "Etiketleme örnekleri oluştur" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Kaydedildi" })).toBeVisible();
  const runs = await (await request.get(`${base}/runs`)).json();
  await waitForRun(request, base, runs.find((r: any) => r.analyzer === "crops-1").id);

  await page.locator("nav").getByRole("button", { name: "Etiketleme" }).click();
  await page.getByLabel("Etiketleyen").fill("e2e-test");
  const progress = page.getByLabel("Etiketleme ilerlemesi");
  await expect(progress).toContainText("0/60 etiketli");
  await expect(page.locator(".label-crop")).toBeVisible();
  await expect(page.locator(".label-context")).toBeVisible();

  const sampleOf = () => page.locator(".label-stage").getAttribute("data-sample");
  const first = await sampleOf();
  // Shortcuts are ignored while typing in a field; move focus to the crop first.
  await page.locator(".label-crop").click();
  await page.keyboard.press("1");
  await expect(progress).toContainText("1/60 etiketli");
  await expect(page.getByLabel("Son işlem")).toContainText("Yemde");
  await page.keyboard.press("2");
  await expect(progress).toContainText("2/60 etiketli");
  const third = await sampleOf();
  await page.keyboard.press("z");
  await expect(progress).toContainText("1/60 etiketli");
  await expect(page.getByLabel("Son işlem")).toContainText("Geri alındı");
  await page.keyboard.press("s");                       // skip: no label, leaves the queue
  await expect(progress).toContainText("1/60 etiketli · 58 açık");
  await page.keyboard.press("3");
  await expect(progress).toContainText("2/60 etiketli");
  expect(first).not.toBe(third);

  const state = await (await request.get(`${base}/labeling/next?camera_id=${camera.id}`)).json();
  expect(state.progress.counts).toEqual({ yemde: 1, yemde_degil: 0, gorunmuyor: 1 });

  await page.getByRole("button", { name: "Yemde süresi" }).click();
  await expect(page.locator(".no-model")).toContainText("Eğitilmiş model yok");
});

test("kesit önizleme: yer tutucu config açıkça işaretli", async ({ page, request }) => {
  const farm = await post(request, "/api/v1/farms", { name: `Önizleme E2E ${Date.now()}` });
  await page.goto("/");
  await page.getByLabel("İşletme").selectOption(farm.id);
  await page.getByRole("button", { name: "Kesit önizleme" }).click();
  await page.getByLabel("Kamera config'i").selectOption("yer_tutucu_test");
  await expect(page.locator(".placeholder-warning")).toContainText("doldurulacak");
  await expect(page.locator(".crop-preview polygon")).toHaveCount(1);
  await page.getByLabel("Kamera config'i").selectOption("synthetic_feeding");
  await expect(page.locator(".crop-preview polygon")).toHaveCount(14);
  await expect(page.locator(".placeholder-warning")).toHaveCount(0);
  await page.getByLabel("Kamera config'i").selectOption("sentetik_test");
  await expect(page.locator(".crop-preview polygon")).toHaveCount(2);
  await expect(page.locator(".placeholder-warning")).toHaveCount(0);
});
