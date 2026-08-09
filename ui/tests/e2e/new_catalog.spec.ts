import { expect, test } from "@playwright/test";

const CORE_URL = process.env.VITE_CORE_URL ?? "http://127.0.0.1:5001";

async function cleanup(name: string) {
  await fetch(`${CORE_URL}/catalogs/${name}`, { method: "DELETE" }).catch(() => {});
}

test.describe("New catalog form", () => {
  test("happy path: register a tpch catalog, land on detail, see it in list", async ({ page }) => {
    const name = "ui_e2e_new";
    await cleanup(name);

    await page.goto("/catalogs");
    await page.getByTestId("new-catalog-button").click();

    await expect(page).toHaveURL(/\/catalogs\/new$/);
    await page.getByLabel("Name").fill(name);
    await page.getByLabel("Connector").fill("tpch");
    await page.getByTestId("submit-button").click();

    // Lands on the detail page for the new catalog.
    await expect(page).toHaveURL(new RegExp(`/catalogs/${name}$`));
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByTestId("status-enabled")).toBeVisible();

    // And the list reflects it too.
    await page.goto("/catalogs");
    await expect(page.getByTestId("catalogs-table")).toContainText(name);

    await cleanup(name);
  });

  test("client-side validation prevents submit with empty fields", async ({ page }) => {
    await page.goto("/catalogs/new");
    await page.getByTestId("submit-button").click();
    await expect(page.getByTestId("error-name")).toBeVisible();
    await expect(page.getByTestId("error-connector")).toBeVisible();
  });

  test("duplicate name returns server error on the name field", async ({ page }) => {
    const name = "ui_e2e_dup";
    await cleanup(name);

    // Pre-register so the second attempt conflicts.
    const created = await fetch(`${CORE_URL}/catalogs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, connector: "tpch" }),
    });
    expect(created.ok).toBeTruthy();

    await page.goto("/catalogs/new");
    await page.getByLabel("Name").fill(name);
    await page.getByLabel("Connector").fill("tpch");
    await page.getByTestId("submit-button").click();

    await expect(page.getByTestId("error-name")).toHaveText(/already exists/i);
    await expect(page).toHaveURL(/\/catalogs\/new$/);

    await cleanup(name);
  });

  test("nonexistent connector is persisted as broken and detail page shows the error", async ({ page }) => {
    const name = "ui_e2e_bad_conn";
    await cleanup(name);

    await page.goto("/catalogs/new");
    await page.getByLabel("Name").fill(name);
    await page.getByLabel("Connector").fill("nonexistent_plugin");
    await page.getByTestId("submit-button").click();

    // Trino rejects → 502 → row persists as broken → still navigates to detail.
    await expect(page).toHaveURL(new RegExp(`/catalogs/${name}$`));
    await expect(page.getByTestId("status-broken")).toBeVisible();
    await expect(page.getByTestId("detail-last-error")).toBeVisible();

    await cleanup(name);
  });
});
