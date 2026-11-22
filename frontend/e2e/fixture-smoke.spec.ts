import { test, expect } from "@playwright/test";

// Frontend-only check with explicit fixture data: every /api call is answered by the test.
const farm = { id: "farm", name: "Tarayıcı testi — SENTETİK DEMO", is_demo: true, timezone: "Europe/Istanbul" };
const payloads: Record<string, unknown> = {
  farms: [farm], barns: [{ id: "barn", name: "Kuzey" }], animals: [{ id: "animal", number: "101", name: "Ada" }],
  stalls: [{ id: "stall", barn_id: "barn", name: "A-01" }],
  cameras: [{ id: "camera", name: "Kamera 1", barn_id: "barn", geometry_version: 1, geometry_valid: false }],
};

test("sahte veriyle panel bölümleri açılır", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const key = url.pathname.split("/").at(-1)!;
    const body = url.pathname === "/api/config" ? { app_name: "İzge", max_upload_bytes: 2147483648 }
      : url.pathname === "/api/admin/experiments" ? { records: [], unreadable_count: 0, note: "" }
      : key === "report" ? { animals: [], timezone: "Europe/Istanbul", status: "insufficient_data" }
      : payloads[key] ?? [];
    return route.fulfill({ json: body });
  });
  await page.goto("/");
  await expect(page.locator(".demo-banner")).toContainText("SENTETİK DEMO");
  const nav = page.locator("nav button");
  const count = await nav.count();
  expect(count).toBeGreaterThanOrEqual(6);
  for (let i = 0; i < count; i++) {
    const label = (await nav.nth(i).innerText()).replace(/^\S+\s*/, "").trim();
    await nav.nth(i).click();
    await expect(page.locator("h1")).toHaveText(label);
  }
  await page.getByRole("button", { name: "İnceleme" }).click();
  await expect(page.getByText("Etiket geçmişi")).toBeVisible();
  await page.getByRole("button", { name: "Saha ayarları" }).click();
  await expect(page.getByText("Kamera bölgeleri")).toBeVisible();
  expect(errors).toEqual([]);
});

test("hatalı yanıt veren sayfa uygulamayı çökertmez", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  // Every admin/control endpoint answers with a wrong shape (an empty array).
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const key = url.pathname.split("/").at(-1)!;
    const body = url.pathname === "/api/config" ? { app_name: "İzge", max_upload_bytes: 2147483648 }
      : key === "report" ? { animals: [], timezone: "Europe/Istanbul", status: "insufficient_data" }
      : payloads[key] ?? [];
    return route.fulfill({ json: body });
  });
  await page.goto("/");
  const nav = page.locator("nav button");
  for (const i of [9, 10, 11, 12, 13]) {
    await nav.nth(i).click();
    await expect(nav).toHaveCount(14);
  }
  await nav.nth(0).click();
  await expect(page.locator("h1")).toHaveText("İşletme özeti");
  expect(errors).toEqual([]);
});
