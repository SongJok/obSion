/** Shared Knowledge citation / provenance helpers. Never invent missing fields. */

export interface KnowledgeProvenanceFields {
  source?: string | null;
  external_id?: string | null;
  revision_id?: string | null;
  connector_name?: string | null;
  operation?: string | null;
  version?: number | string | null;
  title?: string | null;
  chunk_id?: string | null;
}

export interface KnowledgeSearchHit extends KnowledgeProvenanceFields {
  chunk_id: string;
  document_id: string;
  version: number;
  title: string;
  source: string;
  heading_path: string[];
  content: string;
  score: number;
  classification: string;
}

export function provenanceEntries(
  fields: KnowledgeProvenanceFields,
): Array<{ label: string; value: string }> {
  const entries: Array<{ label: string; value: string }> = [];
  if (typeof fields.source === "string" && fields.source.trim()) {
    entries.push({ label: "来源", value: fields.source.trim() });
  }
  if (typeof fields.connector_name === "string" && fields.connector_name.trim()) {
    entries.push({ label: "连接器", value: fields.connector_name.trim() });
  }
  if (typeof fields.external_id === "string" && fields.external_id.trim()) {
    entries.push({ label: "外部 ID", value: fields.external_id.trim() });
  }
  if (fields.revision_id != null && String(fields.revision_id).trim()) {
    entries.push({ label: "修订", value: String(fields.revision_id).trim() });
  }
  if (fields.version != null && String(fields.version).trim()) {
    entries.push({ label: "版本", value: `v${String(fields.version).trim()}` });
  }
  if (typeof fields.operation === "string" && fields.operation.trim()) {
    entries.push({ label: "操作", value: fields.operation.trim() });
  }
  return entries;
}

export function citationLabel(fields: KnowledgeProvenanceFields, index: number): string {
  const title = typeof fields.title === "string" && fields.title.trim() ? fields.title.trim() : "授权文档";
  const source = typeof fields.source === "string" && fields.source.trim() ? fields.source.trim() : "knowledge";
  return `[${index}] ${source} · ${title}`;
}

export function hitsFromEvidenceContent(
  content: Record<string, unknown>,
): KnowledgeProvenanceFields[] {
  const hits = content.hits;
  if (!Array.isArray(hits)) {
    return [
      {
        source: typeof content.source === "string" ? content.source : null,
        external_id: typeof content.external_id === "string" ? content.external_id : null,
        revision_id:
          content.revision_id == null ? null : String(content.revision_id),
        connector_name:
          typeof content.connector_name === "string" ? content.connector_name : null,
        operation: typeof content.operation === "string" ? content.operation : null,
        version: content.version == null ? null : String(content.version),
        title: typeof content.title === "string" ? content.title : null,
        chunk_id: content.chunk_id == null ? null : String(content.chunk_id),
      },
    ].filter((item) => provenanceEntries(item).length > 0 || item.title);
  }
  return hits
    .filter((hit): hit is Record<string, unknown> => !!hit && typeof hit === "object")
    .map((hit) => ({
      source: typeof hit.source === "string" ? hit.source : null,
      external_id: typeof hit.external_id === "string" ? hit.external_id : null,
      revision_id: hit.revision_id == null ? null : String(hit.revision_id),
      connector_name: typeof hit.connector_name === "string" ? hit.connector_name : null,
      operation: typeof hit.operation === "string" ? hit.operation : null,
      version: hit.version == null ? null : String(hit.version),
      title: typeof hit.title === "string" ? hit.title : null,
      chunk_id: hit.chunk_id == null ? null : String(hit.chunk_id),
    }));
}
