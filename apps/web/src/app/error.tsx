"use client";

import { RotateCcw, ShieldAlert } from "lucide-react";
import { useEffect } from "react";

import { Logo } from "@/components/logo";

export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    // 客户端渲染异常只记录诊断标识，不向外部上报任何会话或业务数据。
    console.error("obsion.workbench.render_error", error.digest ?? error.message);
  }, [error]);

  return (
    <main className="route-fallback" role="alert">
      <Logo />
      <span className="route-fallback-icon" aria-hidden="true">
        <ShieldAlert size={26} />
      </span>
      <h1>工作台暂时无法渲染</h1>
      <p>
        页面发生未预期的客户端错误。已提交的 Turn、Run 与审批都持久化在控制面，
        重试不会重复任何操作。
      </p>
      {error.digest && <small>诊断标识 {error.digest}</small>}
      <button type="button" className="primary-button" onClick={() => retry()}>
        <RotateCcw size={16} /> 重试
      </button>
    </main>
  );
}
