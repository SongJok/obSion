import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmptyState } from "../src/components/empty-state";
import { Sidebar } from "../src/components/sidebar";
import type { SessionPrincipal } from "../src/lib/types";

afterEach(cleanup);

function sidebar(collapsed = false) {
  const onView = vi.fn();
  render(<Sidebar
    collapsed={collapsed} onCollapse={vi.fn()} workspaces={[]} onWorkspace={vi.fn()}
    threads={[]} onThread={vi.fn()} onManageThread={vi.fn()} showArchivedThreads={false}
    onToggleArchivedThreads={vi.fn()} onNewThread={vi.fn()} onNewWorkspace={vi.fn()}
    view="assistant" onView={onView}
    principal={{ principal_id: "user-1", organization_id: "org-1", display_name: "测试用户", department: "质量工程", roles: [] } satisfies SessionPrincipal}
    onSignOut={async () => {}}
  />);
  return onView;
}

describe("工作台布局导航", () => {
  it("按操作场景分组且保留所有功能入口", () => {
    const onView = sidebar();
    for (const name of ["日常工作", "工作空间", "企业资源", "开发与治理"]) {
      expect(screen.getByRole("region", { name })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "智能工作台" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "数据目录" }));
    expect(onView).toHaveBeenCalledWith("data");
    expect(screen.getByRole("button", { name: "治理控制台" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeTruthy();
  });

  it("搜索功能支持关键词、空结果和清除后恢复导航", () => {
    const onView = sidebar();
    const search = screen.getByRole("textbox", { name: "搜索功能" });
    fireEvent.change(search, { target: { value: "SQL" } });
    expect(screen.getByRole("button", { name: "工作区 SQL" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "数据目录" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "工作区 SQL" }));
    expect(onView).toHaveBeenCalledWith("sql");
    expect((search as HTMLInputElement).value).toBe("");
    fireEvent.change(search, { target: { value: "不存在的功能" } });
    expect(screen.getByRole("status").textContent).toContain("没有匹配的功能");
    fireEvent.click(screen.getByRole("button", { name: "清除功能搜索" }));
    expect(screen.getByRole("button", { name: "数据目录" })).toBeTruthy();
    fireEvent.change(search, { target: { value: "治理" } });
    fireEvent.keyDown(search, { key: "Escape" });
    expect((search as HTMLInputElement).value).toBe("");
  });

  it("搜索支持分组名称且不丢失功能入口", () => {
    sidebar();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索功能" }), { target: { value: "企业资源" } });
    for (const name of ["企业知识", "企业代码图", "数据目录"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    expect(screen.queryByRole("button", { name: "治理控制台" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "清除功能搜索" }));
    expect(screen.getByRole("button", { name: "治理控制台" })).toBeTruthy();
  });

  it("最近任务位于功能导航之前", () => {
    sidebar();
    const recent = screen.getByText("最近任务");
    const navigation = screen.getByRole("navigation", { name: "主要功能" });
    expect(recent.compareDocumentPosition(navigation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("折叠后图标仍具有可访问名称", () => {
    const onView = sidebar(true);
    expect(screen.getByRole("button", { name: "展开侧边栏" }).getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "企业知识" }));
    expect(onView).toHaveBeenCalledWith("knowledge");
  });

  it("示例只填入问题并明确说明发送前可编辑", () => {
    const onSuggestion = vi.fn();
    render(<EmptyState onSuggestion={onSuggestion} />);
    fireEvent.click(screen.getByRole("button", { name: /分析业务指标/ }));
    expect(onSuggestion).toHaveBeenCalledOnce();
    expect(onSuggestion).toHaveBeenCalledWith("最近 30 天新用户付费率有什么变化？");
    expect(screen.getByText(/修改后再发送/)).toBeTruthy();
  });
});
