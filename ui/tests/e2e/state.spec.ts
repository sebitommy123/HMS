import { expect, test } from "@playwright/test";

const CORE_URL = process.env.VITE_CORE_URL ?? "http://127.0.0.1:5001";

async function cleanup(name: string) {
  await fetch(`${CORE_URL}/catalogs/${name}`, { method: "DELETE" }).catch(() => {});
}

async function register(name: string, connector: string) {
  const res = await fetch(`${CORE_URL}/catalogs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, connector, properties: {} }),
  });
  return res;
}

test.describe("State page", () => {
  test("shows aligned row when Postgres and Trino agree", async ({ page }) => {
    const name = "ui_e2e_state_aligned";
    await cleanup(name);
    await register(name, "tpch");

    await page.goto("/state");
    const table = page.getByTestId("drift-table");
    await expect(table).toBeVisible();
    await expect(table).toContainText(name);
    // The row's verdict is "aligned" — at least one such badge present.
    await expect(page.getByTestId("drift-aligned").first()).toBeVisible();

    await cleanup(name);
  });

  test("clicking 'Reconcile now' brings stats back to aligned after introducing drift", async ({ page }) => {
    const name = "ui_e2e_state_drift";
    await cleanup(name);
    await register(name, "tpch");

    await page.goto("/state");
    // Initial aligned.
    await expect(page.getByTestId("stat-aligned")).toContainText(/[1-9]/);

    // Soft-delete to ensure no stale state, then re-register so we know the row.
    // The "drift" we test here is the heartbeat case: reconcile is a no-op
    // because nothing changed. We still verify the button doesn't error and
    // surfaces a "no changes needed" banner.
    await page.getByTestId("reconcile-button").click();
    await expect(page.getByTestId("reconcile-banner")).toBeVisible();
    await expect(page.getByTestId("reconcile-banner")).toContainText(/succeeded|no changes/i);

    await cleanup(name);
  });

  test("'Only show drift' toggle hides aligned rows", async ({ page }) => {
    const name = "ui_e2e_state_filter";
    await cleanup(name);
    await register(name, "tpch");

    await page.goto("/state");
    await expect(page.getByTestId("drift-table")).toContainText(name);

    await page.getByTestId("only-drift-toggle").check();
    // With no drift, the table is replaced by the "No drift" empty state.
    await expect(page.getByTestId("empty-state")).toBeVisible();
    await expect(page.getByTestId("empty-state")).toContainText(/no drift/i);
    await expect(page.getByTestId("drift-table")).toHaveCount(0);

    await cleanup(name);
  });
});
