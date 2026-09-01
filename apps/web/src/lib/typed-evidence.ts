/**
 * Typed Evidence classification. Dispatch is driven by the persisted
 * `evidence_type` plus the actual `content` envelope the control plane
 * normalizes (`events[]`, `items[]`, `columns/rows`, `plan`, `hits`).
 * Nothing here invents fields: every accessor is a type guard and the
 * generic JSON fallback stays for payloads no classifier recognizes.
 */

export type EvidenceViewKind =
  | "events"
  | "items"
  | "code-items"
  | "data-table"
  | "explain-plan"
  | "knowledge-hits"
  | "document-text"
  | "generic";

export function classifyEvidence(
  evidenceType: string,
  content: Record<string, unknown>,
): EvidenceViewKind {
  if (isRecord(content.plan) && isRecord(content.validation)) {
    return "explain-plan";
  }
  if (Array.isArray(content.events)) {
    return "events";
  }
  if (Array.isArray(content.items)) {
    return evidenceType === "CODE" ? "code-items" : "items";
  }
  if (Array.isArray(content.columns) && Array.isArray(content.rows)) {
    return "data-table";
  }
  if (Array.isArray(content.hits)) {
    return "knowledge-hits";
  }
  if (typeof content.text === "string") {
    return "document-text";
  }
  return "generic";
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function asNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return undefined;
}

export function recordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(isRecord);
}

export function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

/** Display bounds — the control plane already clamps payloads; the UI adds its own ceiling. */
export const MAX_TABLE_ROWS = 100;
export const MAX_LIST_ENTRIES = 200;
export const MAX_ATTRIBUTE_ENTRIES = 12;
export const MAX_META_ENTRIES = 6;

export interface ObservabilityEventView {
  timestamp?: string;
  service?: string;
  environment?: string;
  severity?: string;
  headline?: string;
  detail?: string;
  attributes: Array<{ key: string; value: string }>;
}

/** Map one normalized observability `events[]` entry to a display projection. */
export function observabilityEventView(entry: Record<string, unknown>): ObservabilityEventView {
  const attributes = isRecord(entry.attributes) ? entry.attributes : {};
  const metric = asString(attributes.metric);
  const value = asNumber(attributes.value);
  const unit = asString(attributes.unit);
  const message = asString(attributes.message);
  const spanName = asString(attributes.span_name);
  const durationMs = asNumber(attributes.duration_ms);
  const status = asString(attributes.status) ?? asString(attributes.reason);
  let headline: string | undefined;
  let detail: string | undefined;
  if (message) {
    headline = message;
  } else if (metric) {
    headline = `${metric}${value !== undefined ? ` = ${value}` : ""}${unit ? ` ${unit}` : ""}`;
  } else if (spanName) {
    headline = spanName;
    detail = durationMs !== undefined ? `${durationMs} ms` : undefined;
  } else {
    headline = status;
  }
  const usedKeys = new Set(["message", "metric", "value", "unit", "span_name", "duration_ms", "status", "reason", "kind", "operation"]);
  const rest = Object.entries(attributes)
    .filter(([key]) => !usedKeys.has(key))
    .slice(0, MAX_ATTRIBUTE_ENTRIES)
    .map(([key, raw]) => ({ key, value: formatAttributeValue(raw) }));
  return {
    timestamp: asString(entry.timestamp),
    service: asString(entry.service),
    environment: asString(entry.environment),
    severity: asString(entry.severity),
    headline,
    detail,
    attributes: rest,
  };
}

export interface ChangeItemView {
  title?: string;
  status?: string;
  repository?: string;
  service?: string;
  environment?: string;
  commit?: string;
  timestamp?: string;
  diff?: string;
  configKey?: string;
  previous?: string;
  current?: string;
  attributes: Array<{ key: string; value: string }>;
}

/** Map one normalized engineering `items[]` entry to a display projection. */
export function changeItemView(entry: Record<string, unknown>): ChangeItemView {
  const attributes = isRecord(entry.attributes) ? entry.attributes : {};
  const commit = asString(entry.commit_id);
  const diff = asString(attributes.patch) ?? asString(attributes.diff);
  const previous = attributes.previous === undefined ? undefined : formatAttributeValue(attributes.previous);
  const current = attributes.current === undefined ? undefined : formatAttributeValue(attributes.current);
  const usedKeys = new Set(["patch", "diff", "previous", "current", "key", "message"]);
  const rest = Object.entries(attributes)
    .filter(([key]) => !usedKeys.has(key))
    .slice(0, MAX_ATTRIBUTE_ENTRIES)
    .map(([key, raw]) => ({ key, value: formatAttributeValue(raw) }));
  return {
    title: asString(entry.title) ?? asString(attributes.message),
    status: asString(entry.status),
    repository: asString(entry.repository),
    service: asString(entry.service),
    environment: asString(entry.environment),
    commit,
    timestamp: asString(entry.timestamp),
    diff,
    configKey: asString(attributes.key),
    previous,
    current,
    attributes: rest,
  };
}

export interface CodeItemView {
  name?: string;
  qualifiedName?: string;
  kind?: string;
  path?: string;
  language?: string;
  repository?: string;
  commit?: string;
  startLine?: number;
  endLine?: number;
}

/** Map one Code Graph `items[]` entry to a display projection. */
export function codeItemView(entry: Record<string, unknown>): CodeItemView {
  return {
    name: asString(entry.name),
    qualifiedName: asString(entry.qualified_name),
    kind: asString(entry.kind),
    path: asString(entry.path),
    language: asString(entry.language),
    repository: asString(entry.repository),
    commit: asString(entry.commit_id),
    startLine: asNumber(entry.start_line),
    endLine: asNumber(entry.end_line),
  };
}

export function formatAttributeValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    const serialized = JSON.stringify(value);
    return serialized.length > 160 ? `${serialized.slice(0, 157)}…` : serialized;
  } catch {
    return String(value);
  }
}
