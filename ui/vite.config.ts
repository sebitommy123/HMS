/// <reference types="vitest/config" />
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Reverse-proxy Core and AI under the dev server's own origin so the app talks
// to same-origin /api/core and /api/ai (see ui-dev.sh, which sets VITE_CORE_URL
// / VITE_AI_URL to those paths). This makes the whole stack reachable from a
// single URL with no CORS config and no extra exposed ports. Targets come from
// CORE_URL / AI_URL, which scripts/ui-dev.sh sets to whichever checkout it is
// running in. The fallbacks below are the main clone's stack slot.
const coreTarget = process.env.CORE_URL ?? "http://127.0.0.1:5001";
const aiTarget = process.env.AI_URL ?? "http://127.0.0.1:5002";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: Number(process.env.UI_PORT ?? 5003),
    // Accept requests by hostname (not just 127.0.0.1) so a UI_HOST=0.0.0.0
    // dev server is reachable from other machines without a Host-header reject.
    allowedHosts: true,
    proxy: {
      // SSE streams flow through /api/ai — proxy defaults stream fine.
      "/api/core": {
        target: coreTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/core/, ""),
      },
      "/api/ai": {
        target: aiTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/ai/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    globals: true,
    include: [
      "src/**/*.test.{ts,tsx}",
      "tests/unit/**/*.test.{ts,tsx}",
      "tests/component/**/*.test.{ts,tsx}",
    ],
    exclude: ["tests/e2e/**", "node_modules/**"],
  },
});
