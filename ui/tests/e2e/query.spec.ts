import { expect, test } from "@playwright/test";

const CORE_URL = process.env.VITE_CORE_URL ?? "http://127.0.0.1:5001";

async function cleanup(name: string) {
  await fetch(`${CORE_URL}/catalogs/${name}`, { method: "DELETE" }).catch(() => {});
}

test.describe("Query page", () => {
  test("SHOW CATALOGS round-trip — real Core, real Trino, real rows", async ({ page }) => {
    const name = "ui_e2e_query_cat";
    await cleanup(name);
    const r = await fetch(`${CORE_URL}/catalogs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, connector: "tpch" }),
    });
    expect(r.ok).toBeTruthy();

    await page.goto("/raw-trino-query");
    // The page defaults to SHOW CATALOGS — just hit Run.
    await page.getByTestId("run-button").click();

    const table = page.getByTestId("results-table");
    await expect(table).toBeVisible();
    await expect(table).toContainText(name);
    await expect(table).toContainText("system");

    await cleanup(name);
  });

  test("invalid SQL renders the Trino error panel", async ({ page }) => {
    await page.goto("/raw-trino-query");
    await page.getByTestId("sql-input").fill("SELECT no_such_column FROM system.runtime.nodes");
    await page.getByTestId("run-button").click();

    const panel = page.getByTestId("error-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText(/Trino error/i);
  });

  test("low max_rows triggers the truncation banner", async ({ page }) => {
    const name = "ui_e2e_query_truncate";
    await cleanup(name);
    await fetch(`${CORE_URL}/catalogs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, connector: "tpch" }),
    });

    await page.goto("/raw-trino-query");
    await page.getByTestId("sql-input").fill(`SELECT * FROM ${name}.tiny.nation`);
    await page.getByTestId("max-rows-select").selectOption("100");
    // 100 is well above 25 (nation row count); no truncation expected. Run once
    // to validate the round-trip, then re-run with a stricter constraint.
    await page.getByTestId("run-button").click();
    await expect(page.getByTestId("results-table")).toBeVisible();
    await expect(page.getByTestId("truncation-banner")).toHaveCount(0);

    // Now choose the smallest selectable cap (100) and ask for a bigger table.
    await page.getByTestId("sql-input").fill(`SELECT * FROM ${name}.tiny.lineitem`);
    await page.getByTestId("run-button").click();
    await expect(page.getByTestId("truncation-banner")).toBeVisible();

    await cleanup(name);
  });

  test("picking an example populates the SQL field", async ({ page }) => {
    await page.goto("/raw-trino-query");
    await page.getByTestId("examples-select").selectOption("Trino system metadata");
    await expect(page.getByTestId("sql-input")).toHaveValue(
      /SELECT catalog_name/,
    );
  });
});
