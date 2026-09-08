import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  cleanup();
});

import { ApiError, api } from "@/lib/api";
import { SessionGate } from "@/components/session-gate";
import type { SessionPrincipal } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSession: vi.fn(),
      createSession: vi.fn(),
      createPasswordSession: vi.fn(),
      listWorkspaces: vi.fn(),
    },
  };
});

const getSession = vi.mocked(api.getSession);
const createSession = vi.mocked(api.createSession);
const createPasswordSession = vi.mocked(api.createPasswordSession);

const PRINCIPAL: SessionPrincipal = {
  principal_id: "01a062ea-311d-73cd-93dd-30ca8b540ce3",
  organization_id: "00000000-0000-7000-8000-000000000001",
  display_name: "Song TS",
  department: null,
  roles: ["admin"],
};

function tokenInput() {
  return screen.getByLabelText("访问令牌", { selector: "input" }) as HTMLInputElement;
}

async function renderAnonymousGate() {
  getSession.mockRejectedValue(
    new ApiError("authentication_required", "A bearer token or browser session is required"),
  );
  render(<SessionGate />);
  return waitFor(() => screen.getByRole("tablist", { name: "登录方式" }));
}

describe("session gate credential exchange", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listWorkspaces).mockResolvedValue([]);
  });

  it("offers password login first so a provisioned account can sign in", async () => {
    await renderAnonymousGate();

    expect(screen.getByRole("tab", { name: "账户密码" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(screen.getByLabelText("账户邮箱")).toBeDefined();
    expect(screen.queryByRole("tabpanel", { name: "访问令牌" })).toBeNull();
  });

  it("支持方向键与 Home/End 切换登录方式并保持焦点", async () => {
    await renderAnonymousGate();
    const passwordTab = screen.getByRole("tab", { name: "账户密码" });
    const tokenTab = screen.getByRole("tab", { name: "访问令牌" });
    expect(passwordTab.tabIndex).toBe(0);
    expect(tokenTab.tabIndex).toBe(-1);
    passwordTab.focus();
    for (const [key, target] of [
      ["ArrowLeft", tokenTab], ["ArrowRight", passwordTab],
      ["End", tokenTab], ["Home", passwordTab],
    ] as const) {
      const prevented = !fireEvent.keyDown(document.activeElement!, { key, cancelable: true });
      expect(prevented).toBe(true);
      expect(target.getAttribute("aria-selected")).toBe("true");
      expect(target.tabIndex).toBe(0);
      expect((target === passwordTab ? tokenTab : passwordTab).tabIndex).toBe(-1);
      expect(document.activeElement).toBe(target);
    }
    expect(createSession).not.toHaveBeenCalled();
    expect(createPasswordSession).not.toHaveBeenCalled();
  });

  it("exchanges an email and password for a session", async () => {
    createPasswordSession.mockResolvedValue(PRINCIPAL);
    await renderAnonymousGate();

    fireEvent.change(screen.getByLabelText("账户邮箱"), {
      target: { value: " songts@tuwan.com " },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "phase98-test-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));

    await waitFor(() =>
      expect(createPasswordSession).toHaveBeenCalledWith(
        "songts@tuwan.com",
        "phase98-test-password",
      ),
    );
    expect(createSession).not.toHaveBeenCalled();
  });

  it("keeps the submit button inert until both fields are supplied", async () => {
    await renderAnonymousGate();
    const submit = screen.getByRole("button", { name: /进入工作台/ });

    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("账户邮箱"), {
      target: { value: "songts@tuwan.com" },
    });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "a-password" } });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it("explains a rejected credential without revealing which field was wrong", async () => {
    createPasswordSession.mockRejectedValue(
      new ApiError("invalid_credentials", "The email or password is incorrect"),
    );
    await renderAnonymousGate();

    fireEvent.change(screen.getByLabelText("账户邮箱"), {
      target: { value: "songts@tuwan.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));

    const alert = await waitFor(() => screen.getByRole("alert"));
    expect(alert.textContent).toContain("邮箱或密码不正确");
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
  });

  it("explains a locked credential as a temporary state", async () => {
    createPasswordSession.mockRejectedValue(
      new ApiError("credential_locked", "The credential is locked"),
    );
    await renderAnonymousGate();

    fireEvent.change(screen.getByLabelText("账户邮箱"), {
      target: { value: "songts@tuwan.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));

    const alert = await waitFor(() => screen.getByRole("alert"));
    expect(alert.textContent).toContain("临时锁定");
  });

  it("still exchanges an access token when that mode is selected", async () => {
    createSession.mockResolvedValue(PRINCIPAL);
    await renderAnonymousGate();

    fireEvent.click(screen.getByRole("tab", { name: "访问令牌" }));
    fireEvent.change(tokenInput(), {
      target: { value: "local-development-only-change-me" },
    });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));

    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith("local-development-only-change-me"),
    );
    expect(createPasswordSession).not.toHaveBeenCalled();
  });

  it("clears a stale error and the entered secret when the mode changes", async () => {
    createPasswordSession.mockRejectedValue(
      new ApiError("invalid_credentials", "The email or password is incorrect"),
    );
    await renderAnonymousGate();

    fireEvent.change(screen.getByLabelText("账户邮箱"), {
      target: { value: "songts@tuwan.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));
    await waitFor(() => screen.getByRole("alert"));

    fireEvent.click(screen.getByRole("tab", { name: "访问令牌" }));

    expect(screen.queryByRole("alert")).toBeNull();
    expect(tokenInput().value).toBe("");
  });
});
