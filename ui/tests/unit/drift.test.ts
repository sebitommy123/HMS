import { describe, expect, it } from "vitest";
import { diff, summarize } from "@/lib/drift";
import type { Catalog, TrinoCatalogRow } from "@/api/catalogs";

const ISO = new Date().toISOString();

function cat(name: string, overrides: Partial<Catalog> = {}): Catalog {
  return {
    name,
    connector: "tpch",
    properties: {},
    status: "enabled",
    last_error: null,
    factory_count: 0,
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  };
}

function snap(name: string, connector = "tpch"): TrinoCatalogRow {
  return { name, connector };
}

describe("diff", () => {
  it("aligned when both sides have the same name and connector", () => {
    const rows = diff([cat("a")], [snap("a")]);
    expect(rows).toHaveLength(1);
    expect(rows[0].verdict).toBe("aligned");
  });

  it("missing-in-trino for an enabled catalog Trino doesn't have", () => {
    const rows = diff([cat("a")], []);
    expect(rows[0].verdict).toBe("missing-in-trino");
  });

  it("extra-in-trino for a Trino catalog Core doesn't know about", () => {
    const rows = diff([], [snap("rogue")]);
    expect(rows[0].verdict).toBe("extra-in-trino");
    expect(rows[0].desired).toBeNull();
    expect(rows[0].actual).toEqual(snap("rogue"));
  });

  it("connector-mismatch when names match but connectors differ", () => {
    const rows = diff([cat("a", { connector: "tpch" })], [snap("a", "memory")]);
    expect(rows[0].verdict).toBe("connector-mismatch");
  });

  it("broken rows are flagged broken regardless of Trino state", () => {
    const inTrino = diff([cat("a", { status: "broken" })], [snap("a")]);
    expect(inTrino[0].verdict).toBe("broken");
    const notInTrino = diff([cat("b", { status: "broken" })], []);
    expect(notInTrino[0].verdict).toBe("broken");
  });

  it("disabled + absent from Trino is aligned (not desired anywhere)", () => {
    const rows = diff([cat("a", { status: "disabled" })], []);
    expect(rows[0].verdict).toBe("aligned");
  });

  it("disabled + still in Trino is flagged for drop", () => {
    const rows = diff([cat("a", { status: "disabled" })], [snap("a")]);
    expect(rows[0].verdict).toBe("disabled-but-present");
  });

  it("returns sorted-by-name rows", () => {
    const rows = diff([cat("zeta"), cat("alpha")], [snap("middle")]);
    expect(rows.map((r) => r.name)).toEqual(["alpha", "middle", "zeta"]);
  });

  it("the boss case from core integration tests: missing + extra + mismatch + aligned all at once", () => {
    const desired = [cat("missing"), cat("wrong_conn"), cat("aligned_one")];
    const actual = [
      snap("aligned_one"),
      snap("extra"),
      snap("wrong_conn", "memory"),
    ];
    const rows = diff(desired, actual);
    const byName = Object.fromEntries(rows.map((r) => [r.name, r.verdict]));
    expect(byName).toEqual({
      aligned_one: "aligned",
      extra: "extra-in-trino",
      missing: "missing-in-trino",
      wrong_conn: "connector-mismatch",
    });
  });
});

describe("summarize", () => {
  it("counts by verdict", () => {
    const rows = diff(
      [cat("ok"), cat("miss"), cat("wrong_conn"), cat("bad", { status: "broken" })],
      [snap("ok"), snap("wrong_conn", "memory"), snap("extra")],
    );
    const s = summarize(rows);
    expect(s.total).toBe(5);
    expect(s.aligned).toBe(1);
    expect(s.drift).toBe(4);
    expect(s.byVerdict["missing-in-trino"]).toBe(1);
    expect(s.byVerdict["extra-in-trino"]).toBe(1);
    expect(s.byVerdict["connector-mismatch"]).toBe(1);
    expect(s.byVerdict.broken).toBe(1);
  });
});
