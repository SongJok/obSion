import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TaskContextPicker, taskContextReferences, type TaskContextDraft } from "@/components/task-context-picker";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { knowledgeSearch: vi.fn() } }));
const search = vi.mocked(api.knowledgeSearch);
const hit = { document_id: "doc-one", title: "采购制度", version: 1, chunk_id: "chunk-one",
  source: "native", heading_path: [], content: "正文", score: 1, classification: "INTERNAL" };
beforeEach(() => { vi.clearAllMocks(); search.mockResolvedValue([hit, { ...hit, chunk_id: "chunk-two" }]); });
afterEach(cleanup);

function Controlled() {
  const [value, setValue] = useState<TaskContextDraft>({});
  return <><TaskContextPicker value={value} onChange={setValue} /><output data-testid="refs">{JSON.stringify(taskContextReferences(value))}</output></>;
}

describe("task source and presentation preferences", () => {
  it("deduplicates search hits and submits typed preferences separately from attachment references", async () => {
    render(<Controlled />);
    expect(search).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("设置任务资料与输出"));
    fireEvent.change(screen.getByLabelText("搜索授权资料"), { target: { value: "采购" } });
    fireEvent.click(screen.getByText("搜索资料"));
    const choice = await screen.findByRole("checkbox", { name: "采购制度" });
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.click(choice);
    fireEvent.change(screen.getByLabelText("输出形式"), { target: { value: "TABLE" } });
    fireEvent.change(screen.getByLabelText("补充要求"), { target: { value: "只列复核条件" } });
    expect(JSON.parse(screen.getByTestId("refs").textContent!)).toEqual([{ type: "task_context",
      document_ids: ["doc-one"], output_format: "TABLE", constraints: ["只列复核条件"] }]);
    fireEvent.click(screen.getByText("解除资料限定"));
    expect(JSON.parse(screen.getByTestId("refs").textContent!)[0].document_ids).toEqual([]);
    fireEvent.click(screen.getByText("撤销本轮设置"));
    expect(screen.getByTestId("refs").textContent).toBe("[]");
  });

  it("cancels stale search results after a query or task change", async () => {
    let resolve!: (hits: typeof hit[]) => void;
    const pending = new Promise<typeof hit[]>((done) => { resolve = done; });
    search.mockReturnValueOnce(pending);
    const { rerender } = render(<TaskContextPicker key="one" value={{}} onChange={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("设置任务资料与输出"));
    fireEvent.change(screen.getByLabelText("搜索授权资料"), { target: { value: "采购" } });
    fireEvent.click(screen.getByText("搜索资料"));
    const signal = search.mock.calls[0][1];
    rerender(<TaskContextPicker key="two" value={{}} onChange={vi.fn()} />);
    await act(async () => { resolve([hit]); await pending; });
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("exposes search denial and restores focus when dismissed with Escape", async () => {
    search.mockRejectedValueOnce(new Error("资料访问被拒绝"));
    render(<Controlled />);
    const trigger = screen.getByLabelText("设置任务资料与输出");
    fireEvent.click(trigger);
    fireEvent.change(screen.getByLabelText("搜索授权资料"), { target: { value: "采购" } });
    fireEvent.click(screen.getByText("搜索资料"));
    expect((await screen.findByRole("alert")).textContent).toBe("资料访问被拒绝");
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("retains explicit clearing and omitted inheritance as different contracts", () => {
    expect(taskContextReferences({})).toEqual([]);
    expect(taskContextReferences({ documents: [], constraints: [], output_format: "AUTO" })).toEqual([
      { type: "task_context", document_ids: [], constraints: [], output_format: "AUTO" },
    ]);
  });
});
