export interface KnowledgeSourceConnection {
  connector_id: string;
  binding_id: string;
  name: string;
  corp_id: string;
}
export interface KnowledgeSource {
  id: string;
  connector_id: string;
  name: string;
  corp_id: string;
  active: boolean;
  state: "PAUSED" | "QUEUED" | "SYNCING" | "ATTENTION" | "CURRENT";
  counts: {
    files: number; available: number; pending: number; partial: number;
    failed: number; removed: number; containers: number; awaiting_access_check: number;
  };
  last_scan_completed_at: string | null;
  next_check_at: string;
  issue: string | null;
}
export interface KnowledgeSourceItem {
  id: string; title: string; kind: string; status: string; available: boolean;
  document_id: string | null; revision: string | null; checked_at: string | null; issues: string[];
}
export interface SourcePage<T> { items: T[]; next_cursor: string | null }

export const sourceStateLabels: Record<KnowledgeSource["state"], string> = {
  PAUSED: "已暂停", QUEUED: "等待同步", SYNCING: "正在同步", ATTENTION: "有待处理内容", CURRENT: "已完成本轮同步",
};
export function sourceIssue(code: string): string {
  const messages: Record<string, string> = {
    connection_changed: "连接配置已变化，请重新选择连接登记来源。",
    document_parse_failed: "文档没有可用正文。",
    unsupported_format: "这种文件格式尚不支持自动导入。",
    container: "这是目录，无需作为文档导入。",
    dingtalk_docs_upstream_denied: "当前账号无权读取，请检查文档授权。",
    knowledge_write_denied: "同步权限已变化，请检查账号和来源授权。",
    credential_unavailable: "连接登录或凭据不可用，需要重新检查连接。",
    dingtalk_docs_upstream_unavailable: "暂时无法读取，后台会继续重试。",
    dingtalk_docs_response_invalid: "未能确认完整内容，后台会重新读取。",
    capability_rate_limited: "读取频率受限，稍后继续。",
    resource_not_found: "同步连接尚未配置完整，请联系管理员。",
    artifact_store_unavailable: "文件保存服务暂不可用，后台会继续重试。",
  };
  return messages[code] ?? "文档含有尚未完整支持的内容，暂不用于问答。";
}
