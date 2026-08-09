/**
 * E2E for the chat UI.
 *
 * The basic UI tests (chat list, empty state, navigation) run against the
 * live AI service WITHOUT requiring an ANTHROPIC_API_KEY — they only create
 * and list conversations, which doesn't invoke the LLM.
 *
 * The send-message test is gated on `process.env.ANTHROPIC_API_KEY` being set
 * AND the AI service being reachable. It actually drives Claude end-to-end.
 */

import { expect, test } from "@playwright/test";

const AI_URL = process.env.VITE_AI_URL ?? "http://127.0.0.1:5002";

async function aiAlive(): Promise<boolean> {
  try {
    const r = await fetch(`${AI_URL}/health`);
    // /health returns 200 if everything is fine, 503 if AI itself responded
    // but is degraded — either way AI is reachable.
    return r.status === 200 || r.status === 503;
  } catch {
    return false;
  }
}

async function aiHasKey(): Promise<boolean> {
  try {
    const r = await fetch(`${AI_URL}/health`);
    const body = await r.json();
    return body.anthropic === "configured";
  } catch {
    return false;
  }
}

async function cleanupAllChats() {
  try {
    const r = await fetch(`${AI_URL}/conversations`);
    if (!r.ok) return;
    const rows = (await r.json()) as Array<{ id: string }>;
    for (const row of rows) {
      await fetch(`${AI_URL}/conversations/${row.id}`, { method: "DELETE" }).catch(
        () => {},
      );
    }
  } catch {
    /* ignore */
  }
}

test.describe("Chat UI", () => {
  test.beforeAll(async () => {
    test.skip(!(await aiAlive()), "AI service not reachable");
    await cleanupAllChats();
  });

  test("empty state when no chats exist", async ({ page }) => {
    await cleanupAllChats();
    await page.goto("/chats");
    await expect(page.getByTestId("empty-state")).toBeVisible();
  });

  test("creating a new chat lands on the detail page", async ({ page }) => {
    await cleanupAllChats();
    await page.goto("/chats");
    await page.getByTestId("new-chat-button").click();
    await expect(page).toHaveURL(/\/chats\/[0-9a-f-]+$/);
    await expect(page.getByText(/no messages yet/i)).toBeVisible();
    await cleanupAllChats();
  });

  test("send a message → see tool_use + tool_result + final response", async ({
    page,
  }) => {
    test.skip(!(await aiHasKey()), "ANTHROPIC_API_KEY not configured on AI service");
    await cleanupAllChats();

    await page.goto("/chats");
    await page.getByTestId("new-chat-button").click();
    await expect(page).toHaveURL(/\/chats\/[0-9a-f-]+$/);

    await page
      .getByTestId("message-input")
      .fill("List my registered catalogs. Don't query them.");
    await page.getByTestId("send-button").click();

    // The agent loop runs Anthropic API + Core. Generous timeout — Opus can be
    // slow on a cold first call.
    await expect(page.getByText(/list_catalogs/i)).toBeVisible({ timeout: 90_000 });
    // Tool result should land too.
    await expect(page.getByTestId("tool-result-ok").first()).toBeVisible({
      timeout: 90_000,
    });
    // And the assistant's final natural-language summary.
    await expect(page.getByTestId("message-assistant").last()).toBeVisible();

    await cleanupAllChats();
  });
});
