"use client";

import { ArrowRight, Eye, EyeOff, KeyRound, ShieldCheck } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { AUTHENTICATION_REQUIRED_EVENT } from "@/lib/auth-events";
import type { SessionPrincipal } from "@/lib/types";
import { Logo } from "./logo";
import { Workbench } from "./workbench";

type SessionState = "checking" | "anonymous" | "authenticated";

export function SessionGate() {
  const [state, setState] = useState<SessionState>("checking");
  const [principal, setPrincipal] = useState<SessionPrincipal>();
  const [connectionError, setConnectionError] = useState("");

  useEffect(() => {
    let active = true;
    const requireAuthentication = () => {
      if (!active) return;
      setPrincipal(undefined);
      setState("anonymous");
    };
    window.addEventListener(AUTHENTICATION_REQUIRED_EVENT, requireAuthentication);
    api.getSession()
      .then((session) => {
        if (!active) return;
        setPrincipal(session);
        setState("authenticated");
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (!(caught instanceof ApiError) || !isSessionError(caught.code)) {
          setConnectionError("暂时无法确认会话，请检查控制面连接后重试。");
        }
        setState("anonymous");
      });
    return () => {
      active = false;
      window.removeEventListener(AUTHENTICATION_REQUIRED_EVENT, requireAuthentication);
    };
  }, []);

  const authenticate = useCallback(async (accessToken: string) => {
    const session = await api.createSession(accessToken);
    setConnectionError("");
    setPrincipal(session);
    setState("authenticated");
  }, []);

  const signOut = useCallback(async () => {
    await api.deleteSession();
    setPrincipal(undefined);
    setState("anonymous");
  }, []);

  if (state === "checking") return <SessionChecking />;
  if (state === "authenticated" && principal) {
    return <Workbench principal={principal} onSignOut={signOut} />;
  }
  return <LoginScreen initialError={connectionError} onLogin={authenticate} />;
}

function LoginScreen({
  initialError,
  onLogin,
}: {
  initialError: string;
  onLogin: (accessToken: string) => Promise<void>;
}) {
  const [accessToken, setAccessToken] = useState("");
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(initialError);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const token = accessToken.trim();
    if (!token || pending) return;
    setPending(true);
    setError("");
    try {
      await onLogin(token);
    } catch (caught) {
      setError(loginErrorMessage(caught));
    } finally {
      setAccessToken("");
      setPending(false);
    }
  };

  return (
    <main className="login-shell">
      <section className="login-introduction" aria-labelledby="login-heading">
        <Logo />
        <div className="login-orb" aria-hidden="true"><ShieldCheck size={28} /></div>
        <p className="login-eyebrow">Enterprise Intelligence Workspace</p>
        <h1 id="login-heading">让每一次智能工作，都有边界与证据。</h1>
        <p className="login-summary">
          Obsion 将对话、运行轨迹、工具调用和成本放在同一个工作台中，
          每一次执行都经过统一身份与策略边界。
        </p>
        <ul className="login-assurances" aria-label="安全保证">
          <li><span>01</span><div><strong>统一身份</strong><small>REST 与实时运行流共享同一会话</small></div></li>
          <li><span>02</span><div><strong>可追溯运行</strong><small>Plan、Tool 与 Cost 始终可见</small></div></li>
          <li><span>03</span><div><strong>最小凭据暴露</strong><small>浏览器脚本不会持久化访问令牌</small></div></li>
        </ul>
      </section>

      <section className="login-panel" aria-label="登录 Obsion">
        <form className="login-card" onSubmit={(event) => void submit(event)}>
          <header>
            <span className="login-key"><KeyRound size={19} /></span>
            <div><h2>登录 Obsion</h2><p>使用组织身份提供方签发的访问令牌</p></div>
          </header>
          <label htmlFor="access-token">访问令牌</label>
          <div className="token-input">
            <input
              id="access-token"
              name="access-token"
              type={visible ? "text" : "password"}
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={16_384}
              disabled={pending}
              aria-describedby="token-guidance"
              autoFocus
            />
            <button
              type="button"
              onClick={() => setVisible((current) => !current)}
              aria-label={visible ? "隐藏访问令牌" : "显示访问令牌"}
              aria-pressed={visible}
              disabled={pending}
            >
              {visible ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>
          <p id="token-guidance" className="token-guidance">
            开发环境使用服务端配置的开发令牌；生产环境使用 OIDC access token。
          </p>
          {error && <p className="login-error" role="alert">{error}</p>}
          <button className="login-submit" disabled={pending || !accessToken.trim()}>
            <span>{pending ? "正在建立安全会话…" : "进入工作台"}</span>
            {!pending && <ArrowRight size={17} />}
          </button>
          <footer>
            访问令牌只用于一次会话交换；随后由可撤销的 HttpOnly Cookie 维持登录。
          </footer>
        </form>
      </section>
    </main>
  );
}

function SessionChecking() {
  return (
    <main className="session-checking" aria-live="polite">
      <Logo />
      <i />
      <span>正在确认安全会话…</span>
    </main>
  );
}

function isSessionError(code: string) {
  return ["authentication_required", "invalid_token", "unknown_principal"].includes(code);
}

function loginErrorMessage(caught: unknown) {
  if (caught instanceof ApiError) {
    if (caught.code === "invalid_token") return "访问令牌无效或已过期，请重新获取后再试。";
    if (caught.code === "unknown_principal") return "此身份尚未在 Obsion 中配置，请联系组织管理员。";
    if (caught.code === "request_origin_denied") return "当前页面来源不在控制面允许范围内。";
    return caught.message;
  }
  return caught instanceof Error ? caught.message : "暂时无法登录 Obsion，请稍后重试。";
}
