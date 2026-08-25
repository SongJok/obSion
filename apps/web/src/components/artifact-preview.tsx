import { BarChart3, Code2, FileDiff, FileText, LayoutDashboard, Network, Table2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Artifact } from "@/lib/types";

export function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  const content = artifact.inline_content;
  if (!content) return <p className="artifact-empty">产物内容存储在受控对象存储中，可通过下载获取。</p>;
  if (typeof content.markdown === "string") {
    return (
      <div className="artifact-markdown markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.markdown}</ReactMarkdown>
      </div>
    );
  }
  if (artifact.kind === "SQL" && typeof content.sql === "string") {
    return <pre className="sql-preview"><code>{content.sql}</code></pre>;
  }
  if (artifact.kind === "TABLE") {
    const rows = Array.isArray(content.rows) ? content.rows : [];
    const columns = Array.isArray(content.columns) ? content.columns : Object.keys(rows[0] ?? {});
    return (
      <div className="table-preview">
        <table>
          <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
          <tbody>{rows.slice(0, 20).map((row, index) => (
            <tr key={index}>{columns.map((column) => <td key={column}>{displayValue(row[column])}</td>)}</tr>
          ))}</tbody>
        </table>
        <small>显示 {Math.min(rows.length, 20)} / {content.row_count ?? rows.length} 行</small>
      </div>
    );
  }
  if (artifact.kind === "CHART") return <ChartPreview artifact={artifact} />;
  return <pre className="sql-preview">{JSON.stringify(content, null, 2)}</pre>;
}

function ChartPreview({ artifact }: { artifact: Artifact }) {
  const content = artifact.inline_content;
  const values = content?.data?.values ?? [];
  const xField = content?.encoding?.x?.field;
  const yField = content?.encoding?.y?.field ?? content?.encoding?.text?.field;
  const points = values
    .map((row) => ({ label: xField ? displayValue(row[xField]) : "值", value: Number(yField ? row[yField] : undefined) }))
    .filter((point) => Number.isFinite(point.value));
  const maximum = Math.max(1, ...points.map((point) => Math.abs(point.value)));
  if (!points.length) return <p className="artifact-empty">当前结果没有可绘制的数值列。</p>;
  return (
    <div className="chart-preview">
      {points.slice(0, 20).map((point, index) => (
        <div key={`${point.label}-${index}`}>
          <span>{point.label}</span>
          <i><b style={{ width: `${Math.max(2, Math.abs(point.value) / maximum * 100)}%` }} /></i>
          <strong>{point.value.toLocaleString()}</strong>
        </div>
      ))}
    </div>
  );
}

function displayValue(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function artifactIcon(kind: string, size = 15) {
  if (kind === "TABLE") return <Table2 size={size} />;
  if (kind === "CHART") return <BarChart3 size={size} />;
  if (kind === "SQL" || kind === "CODE") return <Code2 size={size} />;
  if (kind === "DIFF") return <FileDiff size={size} />;
  if (kind === "DASHBOARD") return <LayoutDashboard size={size} />;
  if (kind === "DIAGRAM") return <Network size={size} />;
  return <FileText size={size} />;
}

export function artifactName(kind: string) {
  const names: Record<string, string> = {
    TEXT: "文本",
    TABLE: "结果表",
    CHART: "图表",
    SQL: "已验证 SQL",
    CODE: "代码",
    DIFF: "代码变更",
    REPORT: "报告",
    DASHBOARD: "仪表盘",
    FILE: "文件",
    DIAGRAM: "图示",
  };
  return names[kind] ?? kind;
}
