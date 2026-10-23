import { test, expect } from "@playwright/test";

// prefers-reduced-motion: reduce must leave no running transform animation on the feeding pages.
test("reduced-motion: besleme sayfalarında transform animasyonu yok", async ({ page, request }) => {
  const farms = await (await request.get("/api/v1/farms")).json();
  const demo = farms.find((f: any) => f.is_demo);
  test.skip(!demo, "Demo işletmesi yok: önce python -m izge.demo");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await page.getByLabel("İşletme").selectOption(demo.id);
  for (const name of ["Yemde süresi", "Etiketleme"]) {
    await page.getByRole("button", { name }).click();
    await page.waitForTimeout(500);
    const transformAnimations = await page.evaluate(
      () =>
        document
          .getAnimations()
          .filter((a) =>
            (a.effect as KeyframeEffect | null)
              ?.getKeyframes()
              .some((k) => "transform" in k || "translate" in k || "scale" in k),
          ).length,
    );
    expect(transformAnimations, name).toBe(0);
  }
});
