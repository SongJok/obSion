"use client";

import type { Evidence } from "@/lib/types";
import { citationLabel, hitsFromEvidenceContent } from "@/lib/knowledge-citation";
import { KnowledgeProvenance } from "./knowledge-provenance";
import {
  MAX_LIST_ENTRIES,
  MAX_META_ENTRIES,
  MAX_TABLE_ROWS,
  asString,
  changeItemView,
  classifyEvidence,
  codeItemView,
  formatAttributeValue,
  isRecord,
  observabilityEventView,
  recordArray,
  stringArray,
} from "@/lib/typed-evidence";

/**
 * Typed Evidence content renderer. Dispatches on the persisted envelope
 * shape; unknown payloads keep the raw JSON fallback so no evidence is
 * ever hidden or invented.
 */
export function EvidenceContent({ evidence }: { evidence: Evidence }) {
  const kind = classifyEvidence(evidence.evidence_type, evidence.content);
  switch (kind) {
    case "events":
      return <ObservabilityEvents content={evidence.content} />;
    case "items":
      return <ChangeItems content={evidence.content} operation={asString(evidence.content.operation)} />;
    case "code-items":
      return <CodeItems content={evidence.content} />;
    case "data-table":
      return <DataTable content={evidence.content} />;
    case "explain-plan":
      return <ExplainPlan content={evidence.content} />;
    case "knowledge-hits":
      return <KnowledgeHits content={evidence.content} />;
    case "document-text":
      return <DocumentText content={evidence.content} />;
    default:
      return <RawJson content={evidence.content} />;
  }
}

function RawJson({ content }: { content: Record<string, unknown> }) {
  return <pre className="evidence-raw-json">{JSON.stringify(content, null, 2)}</pre>;
}

function ObservabilityEvents({ content }: { content: Record<string, unknown> }) {
  const events = recordArray(content.events);
  const total = typeof content.count === "number" ? content.count : events.length;
  const visible = events.slice(0, MAX_LIST_ENTRIES);
  return (
    <div className="typed-evidence">
      <TypedHeader operation={asString(content.operation)} total={total} noun="条观测事件" />
      <ul className="ev-events">
        {visible.map((entry, index) => {
          const view = observabilityEventView(entry);
          return (
            <li key={`${view.timestamp ?? "event"}-${index}`}>
              <header>
                {view.severity && <span className={`ev-severity ${view.severity.toLowerCase()}`}>{view.severity}</span>}
                {view.service && <strong>{view.service}</strong>}
                {view.environment && <small>{view.environment}</small>}
                {view.timestamp && <time dateTime={view.timestamp}>{formatTime(view.timestamp)}</time>}
              </header>
              {view.headline && <p>{view.headline}</p>}
              {view.detail && <small className="ev-detail-line">{view.detail}</small>}
              <AttributeChips attributes={view.attributes} />
            </li>
          );
        })}
      </ul>
      <TruncationNote shown={visible.length} total={events.length} />
    </div>
  );
}

function ChangeItems({ content, operation }: { content: Record<string, unknown>; operation?: string }) {
  const items = recordArray(content.items);
  const total = typeof content.count === "number" ? content.count : items.length;
  const visible = items.slice(0, MAX_LIST_ENTRIES);
  const isDiff = operation === "git.diff" || operation === "config.diff";
  return (
    <div className="typed-evidence">
      <TypedHeader operation={operation} total={total} noun="个变更项" />
      <ul className="ev-items">
        {visible.map((entry, index) => {
          const view = changeItemView(entry);
          return (
            <li key={`${view.commit ?? view.configKey ?? "item"}-${index}`}>
              <header>
                {view.status && <span className="ev-status">{view.status}</span>}
                {view.title && <strong>{view.title}</strong>}
                {view.timestamp && <time dateTime={view.timestamp}>{formatTime(view.timestamp)}</time>}
              </header>
              <div className="ev-item-meta">
                {view.repository && <span>{view.repository}</span>}
                {view.service && <span>{view.service}</span>}
                {view.environment && <span>{view.environment}</span>}
                {view.commit && <code title={view.commit}>{view.commit.slice(0, 10)}</code>}
                {view.configKey && <code>{view.configKey}</code>}
              </div>
              {isDiff && view.diff && <pre className="diff-view">{view.diff}</pre>}
              {isDiff && (view.previous !== undefined || view.current !== undefined) && (
                <div className="config-diff">
                  {view.previous !== undefined && (
                    <div><small>变更前</small><code>{view.previous || "（空）"}</code></div>
                  )}
                  {view.current !== undefined && (
                    <div><small>变更后</small><code>{view.current || "（空）"}</code></div>
                  )}
                </div>
              )}
              <AttributeChips attributes={view.attributes} />
            </li>
          );
        })}
      </ul>
      <TruncationNote shown={visible.length} total={items.length} />
    </div>
  );
}

function CodeItems({ content }: { content: Record<string, unknown> }) {
  const items = recordArray(content.items);
  const total = typeof content.count === "number" ? content.count : items.length;
  const visible = items.slice(0, MAX_LIST_ENTRIES);
  return (
    <div className="typed-evidence">
      <TypedHeader operation={asString(content.operation)} total={total} noun="个代码符号" />
      <ul className="ev-code-items">
        {visible.map((entry, index) => {
          const view = codeItemView(entry);
          const location = view.path
            ? `${view.path}${view.startLine !== undefined ? `:${view.startLine}${view.endLine !== undefined && view.endLine !== view.startLine ? `-${view.endLine}` : ""}` : ""}`
            : undefined;
          return (
            <li key={`${view.qualifiedName ?? view.name ?? "symbol"}-${index}`}>
              <header>
                {view.kind && <span className="ev-code-kind">{view.kind}</span>}
                <strong>{view.name ?? view.qualifiedName ?? "（未命名符号）"}</strong>
                {view.language && <small>{view.language}</small>}
              </header>
              {view.qualifiedName && view.qualifiedName !== view.name && (
                <p className="ev-qualified-name">{view.qualifiedName}</p>
              )}
              {location && <code className="ev-location">{location}</code>}
              <div className="ev-item-meta">
                {view.repository && <span>{view.repository}</span>}
                {view.commit && <code title={view.commit}>{view.commit.slice(0, 10)}</code>}
              </div>
            </li>
          );
        })}
      </ul>
      <TruncationNote shown={visible.length} total={items.length} />
    </div>
  );
}

function DataTable({ content }: { content: Record<string, unknown> }) {
  const columns = stringArray(content.columns);
  const rows = recordArray(content.rows);
  const total = typeof content.row_count === "number" ? content.row_count : rows.length;
  const visible = rows.slice(0, MAX_TABLE_ROWS);
  if (!columns.length) {
    return <RawJson content={content} />;
  }
  return (
    <div className="typed-evidence">
      <div className="ev-table-wrap">
        <table className="ev-table">
          <thead>
            <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{formatAttributeValue(row[column]) || "—"}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TruncationNote shown={visible.length} total={total} noun="行" />
    </div>
  );
}

function ExplainPlan({ content }: { content: Record<string, unknown> }) {
  const plan = isRecord(content.plan) ? content.plan : {};
  const validation = isRecord(content.validation) ? content.validation : {};
  return (
    <div className="typed-evidence">
      <section>
        <h4>执行计划</h4>
        <DataTable content={plan} />
      </section>
      {Object.keys(validation).length > 0 && (
        <section>
          <h4>校验结果</h4>
          <dl className="ev-validation">
            {Object.entries(validation).slice(0, MAX_META_ENTRIES).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{formatAttributeValue(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}

function KnowledgeHits({ content }: { content: Record<string, unknown> }) {
  const hits = hitsFromEvidenceContent(content);
  if (!hits.length) {
    return <RawJson content={content} />;
  }
  return (
    <div className="evidence-citations" aria-label="知识引用溯源">
      <strong>引用溯源</strong>
      {hits.map((hit, index) => (
        <div key={`${hit.chunk_id ?? "hit"}-${index}`} className="evidence-citation-item">
          <span>{citationLabel(hit, index + 1)}</span>
          <KnowledgeProvenance fields={hit} compact />
        </div>
      ))}
    </div>
  );
}

function DocumentText({ content }: { content: Record<string, unknown> }) {
  const title = asString(content.title);
  const text = asString(content.text);
  return (
    <div className="typed-evidence ev-document">
      {title && <h4>{title}</h4>}
      {text ? <p>{text}</p> : <RawJson content={content} />}
    </div>
  );
}

function AttributeChips({ attributes }: { attributes: Array<{ key: string; value: string }> }) {
  if (!attributes.length) {
    return null;
  }
  return (
    <div className="ev-attributes">
      {attributes.map(({ key, value }) => (
        <span key={key} title={`${key}: ${value}`}>
          <em>{key}</em>{value || "—"}
        </span>
      ))}
    </div>
  );
}

function TypedHeader({ operation, total, noun }: { operation?: string; total: number; noun: string }) {
  return (
    <header className="typed-evidence-header">
      {operation && <code>{operation}</code>}
      <small>{total} {noun}</small>
    </header>
  );
}

function TruncationNote({ shown, total, noun = "条" }: { shown: number; total: number; noun?: string }) {
  if (shown >= total) {
    return null;
  }
  return <p className="ev-truncation">显示前 {shown} {noun}，共 {total} {noun}（控制面已按预算截断）</p>;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN");
}

/** Full metadata ledger for one Evidence row — every line renders only persisted fields. */
export function EvidenceMeta({ evidence }: { evidence: Evidence }) {
  const rows: Array<{ label: string; value: string; title?: string }> = [
    { label: "观测时间", value: formatTime(evidence.observed_at) },
  ];
  if (evidence.ingested_at) {
    rows.push({ label: "入库时间", value: formatTime(evidence.ingested_at) });
  }
  rows.push({ label: "置信度", value: `${Math.round(Number(evidence.confidence) * 100)}%` });
  rows.push({ label: "数据分级", value: evidence.classification });
  if (evidence.permissions.length) {
    rows.push({ label: "权限动作", value: evidence.permissions.join("、") });
  }
  rows.push({
    label: "内容指纹",
    value: evidence.content_fingerprint.slice(0, 12),
    title: evidence.content_fingerprint,
  });
  if (evidence.step_id) {
    rows.push({ label: "产生步骤", value: evidence.step_id.slice(0, 8), title: evidence.step_id });
  }
  rows.push({ label: "所属 Run", value: evidence.run_id.slice(0, 8), title: evidence.run_id });
  const lineageEntries = Object.entries(evidence.lineage).slice(0, MAX_META_ENTRIES);
  return (
    <div className="evidence-meta">
      <dl>
        {rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd title={row.title}>{row.value}</dd>
          </div>
        ))}
      </dl>
      {lineageEntries.length > 0 && (
        <div className="ev-lineage">
          <small>血缘</small>
          <dl>
            {lineageEntries.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd title={formatAttributeValue(value)}>{formatAttributeValue(value) || "—"}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}
