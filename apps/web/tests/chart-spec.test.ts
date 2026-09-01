import { describe, expect, it } from "vitest";

import {
  MAX_BAR_POINTS,
  MAX_LINE_POINTS,
  buildLineGeometry,
  formatTick,
  parseChartSpec,
} from "@/lib/chart-spec";
import type { ArtifactContent } from "@/lib/types";

function spec(partial: Partial<ArtifactContent> = {}): ArtifactContent {
  return {
    data: {
      values: [
        { day: "2026-08-30", success_rate: 97.2 },
        { day: "2026-08-31", success_rate: 96.8 },
        { day: "2026-09-01", success_rate: 98.1 },
      ],
    },
    mark: { type: "line", point: true, tooltip: true },
    encoding: {
      x: { field: "day", type: "temporal" },
      y: { field: "success_rate", type: "quantitative" },
    },
    ...partial,
  };
}

describe("parseChartSpec", () => {
  it("parses a temporal line spec and sorts points chronologically", () => {
    const reversed = spec({
      data: {
        values: [
          { day: "2026-09-01", success_rate: 98.1 },
          { day: "2026-08-30", success_rate: 97.2 },
          { day: "2026-08-31", success_rate: 96.8 },
        ],
      },
    });
    const chart = parseChartSpec(reversed);
    expect(chart?.mark).toBe("line");
    expect(chart?.temporal).toBe(true);
    expect(chart?.points.map((point) => point.label)).toEqual([
      "2026-08-30",
      "2026-08-31",
      "2026-09-01",
    ]);
  });

  it("keeps nominal bar order and caps the point count", () => {
    const rows = Array.from({ length: 40 }, (_, index) => ({
      channel: `渠道${index}`,
      total: index,
    }));
    const chart = parseChartSpec(
      spec({
        data: { values: rows },
        mark: { type: "bar", tooltip: true },
        encoding: {
          x: { field: "channel", type: "nominal" },
          y: { field: "total", type: "quantitative" },
        },
      }),
    );
    expect(chart?.mark).toBe("bar");
    expect(chart?.temporal).toBe(false);
    expect(chart?.points).toHaveLength(MAX_BAR_POINTS);
    expect(chart?.points[0].label).toBe("渠道0");
  });

  it("parses the text mark for single-number results", () => {
    const chart = parseChartSpec(
      spec({
        data: { values: [{ total: 12345 }] },
        mark: { type: "text", tooltip: true },
        encoding: { y: { field: "total", type: "quantitative" }, text: { field: "total" } },
      }),
    );
    expect(chart?.mark).toBe("text");
    expect(chart?.textField).toBe("total");
    expect(chart?.points).toEqual([{ label: "值", value: 12345, sortKey: 0 }]);
  });

  it("accepts a string mark and falls back to bar for unknown types", () => {
    expect(parseChartSpec(spec({ mark: "bar" }))?.mark).toBe("bar");
    const unknown = parseChartSpec(spec({ mark: { type: "arc" } }));
    expect(unknown?.mark).toBe("bar");
    expect(unknown?.declaredMark).toBe("arc");
  });

  it("rejects content without usable numeric data", () => {
    expect(parseChartSpec(null)).toBeNull();
    expect(parseChartSpec({})).toBeNull();
    expect(parseChartSpec(spec({ data: { values: [] } }))).toBeNull();
    expect(
      parseChartSpec(spec({ data: { values: [{ day: "2026-09-01", success_rate: "high" }] } })),
    ).toBeNull();
    expect(parseChartSpec(spec({ encoding: {} }))).toBeNull();
  });

  it("caps line points at the line maximum", () => {
    const rows = Array.from({ length: MAX_LINE_POINTS + 50 }, (_, index) => ({
      day: `2026-08-${String((index % 30) + 1).padStart(2, "0")}T${String(index).padStart(2, "0")}`,
      total: index,
    }));
    const chart = parseChartSpec(
      spec({
        data: { values: rows },
        encoding: {
          x: { field: "day", type: "temporal" },
          y: { field: "total", type: "quantitative" },
        },
      }),
    );
    expect(chart?.points).toHaveLength(MAX_LINE_POINTS);
  });
});

describe("buildLineGeometry", () => {
  it("maps values into the padded viewport with a path and dots", () => {
    const chart = parseChartSpec(spec());
    const geometry = chart && buildLineGeometry(chart.points, 560, 220, 28);
    expect(geometry).not.toBeNull();
    expect(geometry?.path.startsWith("M")).toBe(true);
    expect(geometry?.dots).toHaveLength(3);
    expect(geometry?.min).toBe(96.8);
    expect(geometry?.max).toBe(98.1);
    const ys = geometry!.dots.map((dot) => dot.y);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(28);
    expect(Math.max(...ys)).toBeLessThanOrEqual(192);
  });

  it("centers a single point and tolerates a flat series", () => {
    const flat = buildLineGeometry(
      [
        { label: "a", value: 5, sortKey: 0 },
        { label: "b", value: 5, sortKey: 1 },
      ],
      560,
      220,
      28,
    );
    expect(flat).not.toBeNull();
    const single = buildLineGeometry([{ label: "a", value: 5, sortKey: 0 }], 560, 220, 28);
    expect(single?.dots[0].x).toBeCloseTo(280);
    expect(buildLineGeometry([], 560, 220, 28)).toBeNull();
  });
});

describe("formatTick", () => {
  it("abbreviates large magnitudes and keeps small values readable", () => {
    expect(formatTick(2_400_000)).toBe("2.4M");
    expect(formatTick(12_500)).toBe("12.5k");
    expect(formatTick(98.1)).toBe("98.1");
    expect(formatTick(0)).toBe("0");
  });
});
