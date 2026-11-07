const agent = { case: emptyCase(), last: null, turns: [], busy: false, shownReport: false };

function emptyCase() {
  return { draft: {}, provenance: {}, answered: [], confirmed: false, history: [], reported: false };
}

function setMode(mode) {
  const isAgent = mode === "agent";
  $("form-mode").hidden = isAgent;
  $("agent-mode").hidden = !isAgent;
  $("tab-form").classList.toggle("active", !isAgent);
  $("tab-agent").classList.toggle("active", isAgent);
  if (isAgent) renderAgent();
}

function bubble(role, text, extra = "") {
  return el("div", { class: `bubble ${role} ${extra}`.trim(), text });
}

function renderChat(pendingText) {
  const log = $("chat-log");
  const nodes = agent.case.history.map((turn) => bubble(turn.role, turn.text));
  if (pendingText) nodes.push(bubble("user", pendingText), bubble("assistant", tr("agent.thinking"), "pending"));
  if (agent.problem) nodes.push(bubble("assistant", agent.problem, "problem"));
  log.replaceChildren(...nodes);
  log.scrollTop = log.scrollHeight;
}

function renderFacts() {
  const rows = (agent.last?.facts ?? []).map((row) => el("tr", { class: row.missing ? "missing" : "" },
    el("td", { class: "item", text: row.label }),
    el("td", { class: "value" }, row.value, row.quote ? el("span", { class: "quote", text: `「${row.quote}」` }) : null),
    el("td", {}, el("span", { class: `src ${row.source}`, text: tr(`agent.source.${row.source}`) }),
      row.assumed ? el("span", { class: "src assumed", text: tr("agent.assumed") }) : null)));
  $("facts-body").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: 3, class: "hint", text: tr("agent.facts.empty") }))]));
  const phase = agent.last?.phase ?? "gather";
  $("phase-badge").textContent = tr(`agent.phase.${phase}`);
  $("confirm").disabled = phase !== "confirm" || agent.busy;
  $("confirm-hint").textContent = phase === "confirm" ? tr("agent.confirm.ready") : phase === "report" ? "" : tr("agent.confirm.wait");
  const notAsked = agent.last?.questions?.not_asked ?? [];
  $("not-asked").replaceChildren(...(notAsked.length
    ? [el("strong", { text: tr("agent.not_asked") }), notAsked.map((q) => q.question).join(" / ")] : []));
}

function renderChips() {
  const groups = (agent.last?.questions?.questions ?? []).map((q) => {
    const answer = (label) => sendMessage(`${q.question} → ${label}`);
    const chip = (label, cls = "") => {
      const button = el("button", { type: "button", class: `chip ${cls}`.trim(), text: label });
      button.addEventListener("click", () => answer(label));
      return button;
    };
    return el("div", { class: "chip-group" }, el("strong", { text: q.question }), el("p", { class: "note", text: q.what_changes }),
      el("div", { class: "chip-row" }, ...q.options.map((o) => chip(o.label)), chip(tr("ui.answer.unknown"), "skip")));
  });
  $("chips").replaceChildren(...groups);
}

function traceEvent(event) {
  if (event.kind === "tool") {
    const head = el("div", { class: "head" }, el("span", { class: "tool", text: event.tool }),
      el("span", { class: event.ok ? "ok" : "no", text: event.ok ? tr("agent.trace.ok") : tr("agent.trace.error") }));
    const rejected = (event.rejected ?? []).length
      ? el("ul", {}, ...event.rejected.map((r) => el("li", { class: "bad" },
        el("strong", { text: `${tr("agent.trace.rejected")}：` }), `${r.path} = ${r.value}　→　${r.error}`)))
      : null;
    return el("div", { class: `ev ${event.ok ? "" : "error"}`.trim() }, head,
      el("code", { text: event.args === "{}" ? "" : event.args }), rejected,
      event.ok ? null : el("code", { text: event.summary }));
  }
  if (event.kind === "guard") {
    const action = event.action === "rewritten" ? tr("agent.trace.rewrite") : tr("agent.trace.fallback");
    return el("div", { class: "ev guard" },
      el("div", { class: "head" }, el("span", { class: "tool", text: tr("agent.trace.guard") }), el("span", { class: "no", text: action })),
      el("ul", {}, ...event.problems.map((kind) => el("li", { class: "bad", text: tr(`agent.trace.problem.${kind}`) }))));
  }
  const key = event.kind === "llm_error" ? "agent.trace.llm_error" : "agent.trace.limit";
  return el("div", { class: "ev error" }, el("span", { class: "no", text: tr(key) }), event.message ? el("code", { text: event.message }) : null);
}

function renderTrace() {
  const turns = agent.turns.map((turn, index) => el("div", { class: "trace-turn" },
    el("h4", { text: `#${index + 1}　${turn.message}` }),
    ...(turn.trace.length ? turn.trace.map(traceEvent) : [el("p", { class: "hint", text: tr("agent.trace.empty") })])));
  $("trace-list").replaceChildren(...turns);
  if (agent.turns.some((t) => t.trace.some((e) => e.kind === "guard" || e.rejected?.length || (e.kind === "tool" && !e.ok)))) {
    $("trace-card").open = true;
  }
}

function renderAgent() {
  renderChat(agent.pending);
  renderFacts();
  renderChips();
  renderTrace();
  const view = agent.last?.report_view;
  if (view) {
    renderResult(view, $("agent-result"), !agent.shownReport);
    agent.shownReport = true;
  } else {
    $("agent-result").hidden = true;
    agent.shownReport = false;
  }
}

function describeFailure(body, status) {
  if (status === 503) return tr("agent.no_key");
  const detail = Array.isArray(body?.detail) ? body.detail.join("; ") : body?.detail ?? status;
  return tr("agent.error", { message: typeof detail === "string" ? detail : JSON.stringify(detail) });
}

async function sendMessage(text) {
  const file = $("chat-file").files[0];
  text = (text ?? "").trim() || (file ? tr("agent.attached", { name: file.name }) : "");
  if (!text || agent.busy) return;
  agent.busy = true;
  agent.problem = null;
  agent.pending = text;
  $("chat-send").disabled = true;
  renderAgent();
  const form = new FormData();
  form.append("payload", JSON.stringify({ case: agent.case, message: text, lang }));
  if (file) form.append("file", file);
  try {
    const response = await fetch("/api/agent/turn", { method: "POST", body: form });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(describeFailure(body, response.status));
    agent.case = body.case;
    agent.last = body;
    agent.turns.push({ message: text, trace: body.trace });
    $("chat-input").value = "";
    $("chat-file").value = "";
    $("chat-file-name").textContent = "";
  } catch (error) {
    agent.problem = error.message;
  } finally {
    agent.busy = false;
    agent.pending = null;
    $("chat-send").disabled = false;
    renderAgent();
  }
}

async function confirmFacts() {
  agent.busy = true;
  renderAgent();
  const response = await fetch(`/api/agent/confirm?lang=${encodeURIComponent(lang)}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ case: agent.case }),
  });
  const body = await response.json().catch(() => null);
  agent.busy = false;
  if (!response.ok) { agent.problem = describeFailure(body, response.status); renderAgent(); return; }
  agent.case = body.case;
  agent.last = { ...agent.last, phase: body.phase, facts: body.facts };
  await sendMessage(tr("agent.confirmed_message"));
}

function resetAgent() {
  Object.assign(agent, { case: emptyCase(), last: null, turns: [], problem: null, pending: null, shownReport: false });
  renderAgent();
}

window.initAgent = function () {
  if (!meta.agent) { $("tab-agent").disabled = true; $("tab-agent").title = tr("agent.no_key"); }
  for (const tab of document.querySelectorAll(".modes .mode")) tab.addEventListener("click", () => setMode(tab.dataset.mode));
  $("chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendMessage($("chat-input").value); });
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage($("chat-input").value); }
  });
  $("chat-file").addEventListener("change", () => { $("chat-file-name").textContent = $("chat-file").files[0]?.name ?? ""; });
  $("confirm").addEventListener("click", confirmFacts);
  $("agent-reset").addEventListener("click", resetAgent);
  renderAgent();
  if (new URLSearchParams(location.search).get("mode") === "agent") setMode("agent");
};

window.onLangChange = () => renderAgent();
