/**
 * ObjectsView — the compact interpreted-objects render. Pure presentational
 * (no fetch/react-query). We assert the lettered legend, per-source grouping,
 * the shared agree/disagree badges, and the per-field hover popup table.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { ObjectsView } from "@/components/ObjectsView";
import type { HmsObject } from "@/api/query";

const A = "cat.sch.a";
const B = "cat.sch.b";

describe("ObjectsView", () => {
  it("renders an empty state when there are no objects", () => {
    render(<ObjectsView objects={[]} />);
    expect(screen.getByTestId("objects-empty")).toBeInTheDocument();
  });

  it("shows the id and a lettered legend entry per data source", () => {
    const obj: HmsObject = {
      data_sources: [A, B],
      id: "6928225076787218553",
      fields: {},
    };
    render(<ObjectsView objects={[obj]} />);
    expect(screen.getByTestId("object-id")).toHaveTextContent("6928225076787218553");
    const card = screen.getByTestId("object-card-0");
    // Legend lists each source with its letter (A first, B second) + full path.
    const legendA = within(card).getByTestId(`legend-${A}`);
    expect(legendA).toHaveTextContent("A");
    expect(legendA).toHaveTextContent(A);
    expect(within(card).getByTestId(`legend-${B}`)).toHaveTextContent("B");
  });

  it("groups single-source fields under their source as flowing badges", () => {
    const obj: HmsObject = {
      data_sources: [A],
      fields: { feedcode: { [A]: "IBKR260814C00110000" } },
    };
    render(<ObjectsView objects={[obj]} />);
    const group = screen.getByTestId(`object-source-group-${A}`);
    const badge = within(group).getByTestId("field-badge-feedcode");
    expect(badge).toHaveTextContent("feedcode");
    expect(badge).toHaveTextContent("IBKR260814C00110000");
    expect(screen.queryByTestId("object-id")).not.toBeInTheDocument();
  });

  it("agreeing shared values show once, with the agreeing letters", () => {
    const obj: HmsObject = {
      data_sources: [A, B],
      id: "id1",
      fields: { price: { [A]: 42.5, [B]: 42.5 } },
    };
    render(<ObjectsView objects={[obj]} />);
    const agree = screen.getByTestId("field-agree-price");
    // Value shown exactly once even though two sources report it.
    expect(agree.textContent?.match(/42\.5/g)?.length).toBe(1);
    // Both letters present (A and B), colorblind-safe reference.
    expect(agree).toHaveTextContent("A");
    expect(agree).toHaveTextContent("B");
    expect(screen.queryByTestId("field-disagree-price")).not.toBeInTheDocument();

    // Hover popup shows BOTH sources ungrouped → the value appears twice there.
    const popup = screen.getByTestId("field-popup-price");
    expect(popup.textContent?.match(/42\.5/g)?.length).toBe(2);
    expect(popup).toHaveTextContent(A);
    expect(popup).toHaveTextContent(B);
  });

  it("disagreeing shared values show each distinct value with its letters", () => {
    const obj: HmsObject = {
      data_sources: [A, B],
      id: "id1",
      fields: { expiry: { [A]: "2026-08-14", [B]: "2026-08-15" } },
    };
    render(<ObjectsView objects={[obj]} />);
    const disagree = screen.getByTestId("field-disagree-expiry");
    expect(disagree).toHaveTextContent("2026-08-14");
    expect(disagree).toHaveTextContent("2026-08-15");
    expect(screen.queryByTestId("field-agree-expiry")).not.toBeInTheDocument();
    // Popup: one row per source, ungrouped.
    const popup = screen.getByTestId("field-popup-expiry");
    expect(popup).toHaveTextContent("2026-08-14");
    expect(popup).toHaveTextContent("2026-08-15");
  });
});
