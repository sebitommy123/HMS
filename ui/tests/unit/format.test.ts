import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { relativeTime, summarizeProperties } from "@/lib/format";

describe("relativeTime", () => {
  const NOW = new Date("2026-06-19T12:00:00Z").getTime();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns 'just now' for sub-5s", () => {
    expect(relativeTime(new Date(NOW - 2_000).toISOString())).toBe("just now");
  });
  it("returns Ns for sub-minute", () => {
    expect(relativeTime(new Date(NOW - 30_000).toISOString())).toBe("30s ago");
  });
  it("returns Nm for sub-hour", () => {
    expect(relativeTime(new Date(NOW - 5 * 60_000).toISOString())).toBe("5m ago");
  });
  it("returns Nh for sub-day", () => {
    expect(relativeTime(new Date(NOW - 3 * 3600_000).toISOString())).toBe("3h ago");
  });
  it("returns Nd for days", () => {
    expect(relativeTime(new Date(NOW - 2 * 86_400_000).toISOString())).toBe("2d ago");
  });
  it("falls back to raw string for invalid input", () => {
    expect(relativeTime("not a date")).toBe("not a date");
  });
});

describe("summarizeProperties", () => {
  it("dash for empty", () => {
    expect(summarizeProperties({})).toBe("—");
  });
  it("returns the key when only one", () => {
    expect(summarizeProperties({ "connection-url": "..." })).toBe("connection-url");
  });
  it("returns count when many", () => {
    expect(summarizeProperties({ a: "1", b: "2", c: "3" })).toBe("3 properties");
  });
});
