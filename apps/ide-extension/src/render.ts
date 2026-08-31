import type { AskResult } from "./runtime.js";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function renderAsk(result: AskResult): string {
  const lines = [
    `# ${result.thread.title}`,
    "",
    result.answer || "(no answer)",
    "",
    `Run ${result.run.id} · ${result.run.status}`,
    `Steps: ${result.steps.map((step) => String(step.kind ?? "")).join(" → ") || "—"}`,
    `Claims: ${result.claims.length} · Evidence: ${result.evidence.length}`,
  ];
  return lines.join("\n");
}

export function containsCredential(text: string, token: string | undefined): boolean {
  if (!token) return false;
  return text.includes(token);
}

export function renderApproval(item: Record<string, unknown>): string {
  return `${String(item.id ?? "")} ${String(item.status ?? "")} ${String(item.capability ?? "")}`.trim();
}

export function redactDump(value: unknown, token: string | undefined): string {
  const raw = JSON.stringify(asRecord(value));
  if (!token) return raw;
  return raw.split(token).join("[redacted]");
}
