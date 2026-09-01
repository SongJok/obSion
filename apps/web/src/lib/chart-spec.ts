import type { ArtifactContent } from "./types";

/**
 * Schema-driven chart helpers: parse the Vega-Lite v5 subset the Harness
 * emits for CHART artifacts (bar / line+point / text marks with x, y, and
 * text encodings), normalize points, and compute SVG line geometry.
 * Pure functions so the Workbench behaviour suite can pin them directly.
 */

export type ChartMark = "bar" | "line" | "text";

export interface ChartPoint {
  label: string;
  value: number;
  sortKey: number;
}

export interface ParsedChart {
  mark: ChartMark;
  declaredMark: string;
  xField?: string;
  xType?: string;
  yField?: string;
  textField?: string;
  points: ChartPoint[];
  temporal: boolean;
}

export const MAX_BAR_POINTS = 20;
export const MAX_LINE_POINTS = 200;

const SUPPORTED_MARKS = new Set<ChartMark>(["bar", "line", "text"]);

export function parseChartSpec(
  content: ArtifactContent | null | undefined,
): ParsedChart | null {
  if (!content || typeof content !== "object") return null;
  const rows = content.data?.values;
  if (!Array.isArray(rows) || rows.length === 0) return null;

  const encoding = content.encoding ?? {};
  const yField = encoding.y?.field;
  const textField = encoding.text?.field;
  const xField = encoding.x?.field;
  const xType = encoding.x?.type;

  const declaredMark = chartMark(content.mark);
  const numericField = yField ?? textField;
  if (!numericField) return null;

  const mark: ChartMark = SUPPORTED_MARKS.has(declaredMark as ChartMark)
    ? (declaredMark as ChartMark)
    : "bar";
  const temporal = xType === "temporal";

  const points: ChartPoint[] = [];
  rows.forEach((row, index) => {
    if (!row || typeof row !== "object") return;
    const value = Number(row[numericField]);
    if (!Number.isFinite(value)) return;
    const rawLabel = xField ? row[xField] : undefined;
    const label = rawLabel === null || rawLabel === undefined ? "值" : displayValue(rawLabel);
    points.push({ label, value, sortKey: temporal ? temporalKey(rawLabel, index) : index });
  });
  if (!points.length) return null;

  const ordered = temporal ? [...points].sort((a, b) => a.sortKey - b.sortKey) : points;
  const cap = mark === "line" ? MAX_LINE_POINTS : MAX_BAR_POINTS;

  return {
    mark,
    declaredMark,
    xField,
    xType,
    yField,
    textField,
    points: mark === "text" ? ordered : ordered.slice(0, cap),
    temporal,
  };
}

function chartMark(mark: ArtifactContent["mark"]): string {
  if (typeof mark === "string") return mark;
  if (mark && typeof mark === "object" && typeof mark.type === "string") return mark.type;
  return "bar";
}

function temporalKey(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) return parsed;
  }
  return fallback;
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export interface LineGeometry {
  path: string;
  dots: Array<{ x: number; y: number; point: ChartPoint }>;
  yTicks: number[];
  min: number;
  max: number;
}

export function buildLineGeometry(
  points: ChartPoint[],
  width: number,
  height: number,
  padding: number,
): LineGeometry | null {
  if (!points.length) return null;
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.abs(max) || 1;
  const innerWidth = Math.max(1, width - padding * 2);
  const innerHeight = Math.max(1, height - padding * 2);
  const step = points.length > 1 ? innerWidth / (points.length - 1) : 0;

  const dots = points.map((point, index) => {
    const x = points.length > 1 ? padding + step * index : padding + innerWidth / 2;
    const y = padding + innerHeight - ((point.value - min) / span) * innerHeight;
    return { x: round(x), y: round(y), point };
  });
  const path = dots
    .map((dot, index) => `${index === 0 ? "M" : "L"}${dot.x},${dot.y}`)
    .join(" ");

  const yTicks = [max, round(min + span / 2), min];
  return { path, dots, yTicks, min, max };
}

export function formatTick(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${round(value / 1_000_000)}M`;
  if (abs >= 10_000) return `${round(value / 1_000)}k`;
  return String(round(value));
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
