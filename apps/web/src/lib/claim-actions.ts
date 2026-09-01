import type { Claim, Evidence } from "./types";

/**
 * Post-conclusion context actions: turn a verified claim into a workspace
 * task or a decision record, prefilled from the claim statement, its
 * verification status, and the supporting evidence — always carrying the
 * source Run so provenance survives into the collaboration ledger.
 * Pure functions so the Workbench behaviour suite can pin them directly.
 */

export const CLAIM_TITLE_MAX = 60;
export const CLAIM_EVIDENCE_LINES_MAX = 5;

export function truncateClaim(statement: string, max = CLAIM_TITLE_MAX): string {
  const compact = statement.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  return `${compact.slice(0, max - 1).trimEnd()}…`;
}

export function claimActionTitle(claim: Claim, index: number): string {
  return `结论 C${index + 1}：${truncateClaim(claim.statement)}`;
}

export function claimEvidenceLines(
  claim: Claim,
  evidence: Evidence[],
  max = CLAIM_EVIDENCE_LINES_MAX,
): string[] {
  const lines: string[] = [];
  for (const id of claim.evidence_ids) {
    const item = evidence.find((entry) => entry.id === id);
    if (!item) continue;
    lines.push(`${item.evidence_type} · ${item.source} — ${item.resource}`);
    if (lines.length >= max) break;
  }
  return lines;
}

export function claimTaskPayload(
  claim: Claim,
  runId: string,
  index: number,
): Record<string, unknown> {
  const description = [
    claim.statement.trim(),
    "",
    `来源：Run ${runId.slice(0, 8)} · ${claim.evidence_ids.length} 项证据支撑 · 验证状态 ${claim.verification_status}`,
  ].join("\n");
  return {
    title: claimActionTitle(claim, index),
    description,
    source_run_id: runId,
  };
}

export function claimDecisionPayload(
  claim: Claim,
  evidence: Evidence[],
  runId: string,
  index: number,
): Record<string, unknown> {
  const lines = claimEvidenceLines(claim, evidence);
  const rationale = [
    `该结论经 Critic 验证（${claim.verification_status}，置信度 ${claim.confidence}），由 ${claim.evidence_ids.length} 项证据支撑：`,
    ...lines.map((line) => `- ${line}`),
    ...(claim.evidence_ids.length > lines.length
      ? [`- 另有 ${claim.evidence_ids.length - lines.length} 项证据见运行详情`]
      : []),
  ].join("\n");
  return {
    title: claimActionTitle(claim, index),
    summary: truncateClaim(claim.statement, 240),
    rationale,
    source_run_id: runId,
  };
}
