import { expect, test } from "@playwright/test";

test("home page renders and the health indicator reaches a verdict", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /^Overview$/i })).toBeVisible();

  const health = page.getByTestId("health-indicator");
  await expect(health).toBeVisible();
  // It eventually settles to ok / degraded — not still "checking…".
  await expect(health).not.toContainText(/checking/i, { timeout: 15_000 });
});
