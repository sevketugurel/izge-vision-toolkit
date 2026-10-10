import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run against a real API + worker + PostgreSQL 18 stack
// (scripts/verify.sh starts a disposable Compose project). IZGE_E2E_URL points at it.
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  outputDir: "../artifacts/playwright",
  use: {
    baseURL: process.env.IZGE_E2E_URL || "http://127.0.0.1:8000",
    trace: "retain-on-failure",
    locale: "tr-TR",
    timezoneId: "Europe/Istanbul",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
