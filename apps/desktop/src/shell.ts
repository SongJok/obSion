export const DESKTOP_SHELL_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Obsion Desktop</title>
  <style>
    :root { color-scheme: light; --ink:#1b1d24; --muted:#5b616b; --line:#e4e1f2; --brand:#5b4fd8; --bg:#f6f5fb; }
    * { box-sizing: border-box; }
    body { margin: 0; font: 13px/1.45 ui-sans-serif, system-ui, sans-serif; color: var(--ink); background: var(--bg); }
    header { display: flex; justify-content: space-between; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--line); background: #fff; }
    header strong { font-size: 14px; }
    header small { color: var(--muted); }
    main { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.8fr); min-height: calc(100dvh - 52px); }
    section { padding: 16px 18px; }
    .composer { display: grid; gap: 8px; }
    textarea, input, button { font: inherit; }
    textarea { width: 100%; min-height: 88px; padding: 10px; border: 1px solid var(--line); border-radius: 10px; resize: vertical; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; }
    button { min-height: 34px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: #fff; }
    button.secondary { background: #fff; color: var(--ink); border-color: var(--line); }
    pre { white-space: pre-wrap; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px; min-height: 180px; }
    .aside { border-left: 1px solid var(--line); background: #fff; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 11px; font-weight: 600; }
    input { min-height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 8px; }
    .status { margin: 10px 0 0; color: var(--muted); font-size: 11px; }
    .error { color: #a54343; }
  </style>
</head>
<body>
  <header>
    <div><strong>Obsion Desktop</strong><div><small>App Server 客户端 · 不实现 Harness</small></div></div>
    <small id="connection">未连接</small>
  </header>
  <main>
    <section>
      <form class="composer" id="ask-form">
        <label>问题<textarea name="question" required placeholder="询问知识、指标、代码，或调查线上异常…"></textarea></label>
        <div class="row">
          <button type="submit">提问</button>
          <button class="secondary" type="button" id="cancel">取消 Run</button>
          <button class="secondary" type="button" id="replay">重放</button>
        </div>
      </form>
      <pre id="output">等待提问。答案、步骤、Claims 与 Evidence 会显示在这里。</pre>
    </section>
    <aside class="aside">
      <section>
        <form id="token-form">
          <label>访问令牌（写入本机 secret 文件，不进配置）<input name="token" type="password" autocomplete="off" /></label>
          <div class="row" style="margin-top:8px">
            <button type="submit">保存令牌</button>
            <button class="secondary" type="button" id="clear-token">清除</button>
          </div>
        </form>
        <p class="status" id="token-status"></p>
        <form id="approve-form" style="margin-top:18px">
          <label>审批说明<input name="reason" placeholder="Approved from Desktop" /></label>
          <div class="row" style="margin-top:8px">
            <button type="submit">批准</button>
            <button class="secondary" type="button" id="reject">拒绝</button>
          </div>
        </form>
        <p class="status" id="notice"></p>
      </section>
    </aside>
  </main>
  <script>
    const output = document.getElementById("output");
    const notice = document.getElementById("notice");
    const connection = document.getElementById("connection");
    const tokenStatus = document.getElementById("token-status");
    async function api(path, options) {
      const response = await fetch(path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        ...options,
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "请求失败");
      return body;
    }
    async function refresh() {
      const status = await api("/api/status");
      connection.textContent = status.baseUrl;
      tokenStatus.textContent = status.hasToken ? "已保存令牌" : "尚未保存令牌";
    }
    document.getElementById("ask-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      notice.textContent = "";
      const question = event.target.question.value;
      try {
        const result = await api("/api/ask", { method: "POST", body: JSON.stringify({ text: question }) });
        output.textContent = result.rendered;
      } catch (error) {
        notice.className = "status error";
        notice.textContent = error.message;
      }
    });
    document.getElementById("token-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const token = event.target.token.value;
      await api("/api/token", { method: "POST", body: JSON.stringify({ token }) });
      event.target.token.value = "";
      await refresh();
    });
    document.getElementById("clear-token").addEventListener("click", async () => {
      await api("/api/token", { method: "DELETE" });
      await refresh();
    });
    document.getElementById("cancel").addEventListener("click", () => api("/api/cancel", { method: "POST", body: "{}" }).then((body) => { output.textContent = body.rendered; }));
    document.getElementById("replay").addEventListener("click", () => api("/api/replay", { method: "POST", body: "{}" }).then((body) => { output.textContent = body.rendered; }));
    document.getElementById("approve-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const reason = event.target.reason.value;
      const body = await api("/api/approve", { method: "POST", body: JSON.stringify({ reason }) });
      output.textContent = body.rendered;
    });
    document.getElementById("reject").addEventListener("click", async () => {
      const reason = document.querySelector("#approve-form input[name=reason]").value;
      const body = await api("/api/reject", { method: "POST", body: JSON.stringify({ reason }) });
      output.textContent = body.rendered;
    });
    refresh().catch((error) => { notice.className = "status error"; notice.textContent = error.message; });
  </script>
</body>
</html>
`;
