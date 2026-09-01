import { BarChart3, Code2, FileDiff, FileText, LayoutDashboard, Network, Table2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Artifact } from "@/lib/types";
import {
  buildLineGeometry,
  formatTick,
  parseChartSpec,
  type ParsedChart,
} from "@/lib/chart-spec";

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
  if (artifact.kind === "DASHBOARD") {
    const panels = Array.isArray(content.panels) ? content.panels : [];
    return (
      <div className="dashboard-preview">
        <p>此仪表盘只引用已发布的 CHART / TABLE / SQL，不包含自造数据系列。</p>
        <ul>
          {panels.map((panel) => {
            const item = panel as { artifact_id?: string; kind?: string; title?: string };
            return (
              <li key={item.artifact_id ?? item.title}>
                {item.kind} · {item.title}
              </li>
            );
          })}
        </ul>
      </div>
    );
  }
  return <pre className="sql-preview">{JSON.stringify(content, null, 2)}</pre>;
}

function ChartPreview({ artifact }: { artifact: Artifact }) {
  const chart = parseChartSpec(artifact.inline_content);
  if (!chart) return <p className="artifact-empty">当前结果没有可绘制的数值列。</p>;
  if (chart.mark === "text") return <ChartText chart={chart} />;
  if (chart.mark === "line") return <ChartLine chart={chart} />;
  return <ChartBars chart={chart} />;
}

function ChartBars({ chart }: { chart: ParsedChart }) {
  const maximum = Math.max(1, ...chart.points.map((point) => Math.abs(point.value)));
  return (
    <div className="chart-preview">
      {chart.points.map((point, index) => (
        <div key={`${point.label}-${index}`}>
          <span>{point.label}</span>
          <i><b style={{ width: `${Math.max(2, Math.abs(point.value) / maximum * 100)}%` }} /></i>
          <strong>{point.value.toLocaleString()}</strong>
        </div>
      ))}
    </div>
  );
}

function ChartText({ chart }: { chart: ParsedChart }) {
  return (
    <div className="chart-big-number">
      {chart.points.slice(0, 4).map((point, index) => (
        <article key={`${point.label}-${index}`}>
          <strong>{point.value.toLocaleString()}</strong>
          <span>{chart.yField ?? chart.textField ?? point.label}</span>
        </article>
      ))}
    </div>
  );
}

const LINE_WIDTH = 560;
const LINE_HEIGHT = 220;
const LINE_PADDING = 28;

function ChartLine({ chart }: { chart: ParsedChart }) {
  const geometry = buildLineGeometry(chart.points, LINE_WIDTH, LINE_HEIGHT, LINE_PADDING);
  if (!geometry) return <p className="artifact-empty">当前结果没有可绘制的数值列。</p>;
  const first = chart.points[0];
  const last = chart.points[chart.points.length - 1];
  return (
    <div className="chart-line-wrap">
      <svg className="chart-line" viewBox={`0 0 ${LINE_WIDTH} ${LINE_HEIGHT}`} role="img" aria-label={chart.yField ?? "折线图"}>
        {geometry.yTicks.map((tick, index) => {
          const y = LINE_PADDING + (LINE_HEIGHT - LINE_PADDING * 2) * (index / (geometry.yTicks.length - 1));
          return (
            <g key={tick}>
              <line className="chart-grid" x1={LINE_PADDING} x2={LINE_WIDTH - LINE_PADDING} y1={y} y2={y} />
              <text className="chart-tick" x={LINE_PADDING - 6} y={y + 3}>{formatTick(tick)}</text>
            </g>
          );
        })}
        <path className="chart-line-path" d={geometry.path} />
        {geometry.dots.map((dot, index) => (
          <circle key={`${dot.point.label}-${index}`} className="chart-line-dot" cx={dot.x} cy={dot.y} r={3}>
            <title>{`${dot.point.label}：${dot.point.value.toLocaleString()}`}</title>
          </circle>
        ))}
      </svg>
      <div className="chart-line-axis"><span>{first.label}</span><span>{last.label}</span></div>
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
