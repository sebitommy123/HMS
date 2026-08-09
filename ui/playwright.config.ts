import { defineConfig } from "@playwright/test";

/**
 * Playwright config for ui/ e2e tests.
 *
 * Assumes Core is already running at the configured URL (we do NOT spin it up
 * here — Core has its own docker compose and live Postgres + Trino requirements
 * that are out of scope for the UI test runner).
 *
 * The webServer block spins up the UI itself (vite preview against the built
 * bundle for CI; vite dev for local iteration).
 */
const CORE_URL = process.env.VITE_CORE_URL ?? "http://127.0.0.1:5001";

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false, // tests within a file run serially
  workers: 1, // and only one file at a time — all tests share a single Core
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "on-first-retry",
  },
  webServer: {
    command: "pnpm preview --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      VITE_CORE_URL: CORE_URL,
    },
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
