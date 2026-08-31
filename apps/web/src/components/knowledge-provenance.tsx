"use client";

import { Link2 } from "lucide-react";

import {
  KnowledgeProvenanceFields,
  provenanceEntries,
} from "@/lib/knowledge-citation";

export function KnowledgeProvenance({
  fields,
  compact = false,
}: {
  fields: KnowledgeProvenanceFields;
  compact?: boolean;
}) {
  const entries = provenanceEntries(fields);
  if (!entries.length) {
    return (
      <p className="citation-provenance empty">
        未记录连接器溯源字段（不编造）
      </p>
    );
  }
  return (
    <dl className={`citation-provenance ${compact ? "compact" : ""}`}>
      {entries.map((entry) => (
        <div key={`${entry.label}:${entry.value}`}>
          <dt>
            <Link2 size={12} aria-hidden />
            {entry.label}
          </dt>
          <dd title={entry.value}>{entry.value}</dd>
        </div>
      ))}
    </dl>
  );
}
