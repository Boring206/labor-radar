const WEEKDAYS = ["unknown", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const FIELDS = ["relationship", "shift_system", "special_hours_arrangement", "responsibility_system",
  "overtime_consent", "pay_basis", "week_starts_on"];
let strings = {};
let meta = null;
let lang = localStorage.getItem("lang") || "zh-TW";

const $ = (id) => document.getElementById(id);
const tr = (key, params = {}) =>
  (strings[key] ?? key).replace(/\{(\w+)\}/g, (_, name) => params[name] ?? "");
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
};

async function loadStrings() {
  strings = await (await fetch(`/api/i18n/${lang}`)).json();
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = tr(node.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => { node.placeholder = tr(node.dataset.i18nPlaceholder); });
  document.querySelectorAll("select[data-options]").forEach((select) => {
    const current = select.value;
    select.replaceChildren(...select.dataset.values.split(",").map((value) =>
      el("option", { value, text: tr(`${select.dataset.options}.${value}`) })));
    if (current) select.value = current;
  });
  const week = $("week_starts_on");
  const currentWeek = week.value || "unknown";
  week.replaceChildren(...WEEKDAYS.map((d) => el("option", { value: d, text: tr(`ui.weekday.${d}`) })));
  week.value = currentWeek;
  const info = meta.languages.find((l) => l.code === lang);
  $("draft-banner").hidden = !info?.draft;
  $("draft-banner").textContent = tr("ui.draft_banner");
}

function shiftRow(shift = { date: "", start: "", end: "", break_minutes: 0 }) {
  const input = (type, name, value) => el("input", { type, name, value: value ?? "" });
  const remove = el("button", { type: "button", class: "link", text: tr("ui.remove") });
  const row = el("tr", {},
    el("td", {}, input("date", "date", shift.date)),
    el("td", {}, input("time", "start", shift.start)),
    el("td", {}, input("time", "end", shift.end)),
    el("td", {}, input("number", "break_minutes", shift.break_minutes ?? 0)),
    el("td", {}, remove));
  remove.addEventListener("click", () => row.remove());
  return row;
}

function fillForm(record) {
  $("worker_name").value = record.worker?.name ?? "";
  $("employer_name").value = record.employer?.name ?? "";
  $("city").value = record.city ?? "";
  $("period_start").value = record.period?.start ?? "";
  $("period_end").value = record.period?.end ?? "";
  const job = record.employment ?? {};
  $("agreed_wage").value = job.agreed_wage ?? "";
  $("agreed_monthly_hours").value = job.agreed_monthly_hours ?? "";
  $("domestic_worker").checked = !!job.domestic_worker;
  for (const field of FIELDS) if (job[field]) $(field).value = job[field];
  $("shift-rows").replaceChildren(...(record.shifts ?? []).map(shiftRow));
  const slip = record.payslips?.[0];
  $("gross_pay").value = slip?.gross_pay ?? "";
  $("self_reported").checked = !!slip?.self_reported;
  $("json").value = JSON.stringify(record, null, 2);
}

function readForm() {
  const employment = { agreed_wage: Number($("agreed_wage").value), domestic_worker: $("domestic_worker").checked };
  for (const field of FIELDS) employment[field] = $(field).value;
  if ($("agreed_monthly_hours").value !== "") employment.agreed_monthly_hours = Number($("agreed_monthly_hours").value);
  const period = { start: $("period_start").value, end: $("period_end").value };
  const shifts = [...$("shift-rows").querySelectorAll("tr")].map((row) => {
    const value = (name) => row.querySelector(`[name=${name}]`).value;
    return { date: value("date"), start: value("start"), end: value("end"),
      break_minutes: Number(value("break_minutes") || 0) };
  }).filter((s) => s.date && s.start && s.end);
  const record = {
    worker: { name: $("worker_name").value || "—" },
    employer: { name: $("employer_name").value || "—" },
    city: $("city").value, employment, period, shifts, payslips: [],
  };
  const base = safeJson($("json").value);
  if (base) {
    record.worker = { ...base.worker, name: record.worker.name };
    record.employer = { ...base.employer, name: record.employer.name };
  }
  if ($("gross_pay").value !== "") {
    record.payslips = [{ period, gross_pay: Number($("gross_pay").value), self_reported: $("self_reported").checked }];
  }
  return record;
}

const safeJson = (text) => { try { return JSON.parse(text); } catch { return null; } };

function showErrors(problems) {
  const box = $("errors");
  box.hidden = !problems.length;
  box.replaceChildren(el("strong", { text: tr("ui.error") }), el("ul", {}, ...problems.map((p) => el("li", { text: p }))));
}

function findingCard(item) {
  const citations = el("p", { class: "citations" }, `${tr("label.citations")}：`,
    ...item.citations.flatMap((c, i) => [i ? "、" : "", el("a", { href: c.url, target: "_blank", rel: "noopener", text: c.label })]));
  const calcs = item.calcs.length
    ? el("table", { class: "calc" }, ...item.calcs.map((c) => el("tr", {}, el("td", { text: c.text }), el("td", { class: "num", text: `${c.amount} ${tr("label.ntd")}` }))))
    : null;
  const why = item.why_human.length
    ? el("div", { class: "why" }, el("strong", { text: tr("label.why_human") }), el("ul", {}, ...item.why_human.map((w) => el("li", { text: w }))))
    : null;
  return el("article", { class: `finding ${item.verdict}` },
    el("header", {}, el("span", { class: "badge", text: tr(`verdict.${item.verdict}`) }), el("h4", { text: item.title }),
      item.amount ? el("span", { class: "amount", text: `${item.amount} ${tr("label.ntd")}` }) : null),
    el("p", { text: item.explanation }), why, calcs, citations);
}

function section(title, ...children) {
  return el("section", { class: "card result-part" }, el("h3", { text: title }), ...children);
}

function renderResult(view, target = $("result"), scroll = true) {
  const parts = [section(tr("section.summary"), ...view.summary.map((s) => el("p", { class: "summary", text: s })))];
  for (const [key, title] of [["violations", "section.violations"], ["needs_human", "section.needs_human"], ["notes", "section.notes"]]) {
    if (view[key].length) parts.push(section(tr(title), ...view[key].map(findingCard)));
  }
  if (!view.violations.length && !view.needs_human.length) parts.push(section(tr("section.violations"), el("p", { text: tr("summary.none") })));
  const claim = view.claim;
  if (claim.complete) {
    const rows = claim.lines.map((l) => el("tr", {}, el("td", { text: l.text }), el("td", { class: "num", text: `${l.amount} ${tr("label.ntd")}` })));
    rows.push(el("tr", { class: "total" }, el("td", { text: tr("claim.required") }), el("td", { class: "num", text: `${claim.required} ${tr("label.ntd")}` })));
    rows.push(el("tr", {}, el("td", { text: claim.paid_label }), el("td", { class: "num", text: `− ${claim.paid} ${tr("label.ntd")}` })));
    rows.push(el("tr", { class: "total big" }, el("td", { text: tr("claim.shortfall") }), el("td", { class: "num", text: `${Number(claim.shortfall).toLocaleString()} ${tr("label.ntd")}` })));
    parts.push(section(tr("section.claim"), el("table", { class: "calc" }, ...rows),
      claim.possible_extra ? el("p", { class: "hint", text: `${claim.possible_label}：${claim.possible_extra} ${tr("label.ntd")}` }) : null,
      claim.settled_message ? el("p", { text: claim.settled_message }) : null,
      el("p", { class: "hint", text: claim.rate_note })));
  } else {
    parts.push(section(tr("section.claim"), el("p", { text: claim.message })));
  }
  parts.push(section(tr("section.evidence"), el("ul", { class: "checklist" },
    ...view.evidence.map((e) => el("li", {}, el("label", {}, el("input", { type: "checkbox" }), ` ${e}`))))));
  if (view.not_checked.length) parts.push(section(tr("section.not_checked"), el("ul", {}, ...view.not_checked.map((n) => el("li", { text: n })))));
  const download = el("button", { type: "button", class: "secondary", text: tr("ui.download_draft") });
  download.addEventListener("click", () => {
    const blob = new Blob([view.mediation_draft], { type: "text/plain;charset=utf-8" });
    const link = el("a", { href: URL.createObjectURL(blob), download: "勞資爭議調解申請書草稿.txt" });
    link.click();
  });
  parts.push(section(tr("section.mediation"), el("pre", { class: "draft", text: view.mediation_draft }), download));
  parts.push(section(tr("section.referrals"), el("ul", { class: "referrals" }, ...view.referrals.map((r) =>
    el("li", {}, el("strong", { text: r.name }), el("p", { text: r.detail }), el("a", { href: r.url, target: "_blank", rel: "noopener", text: r.url }))))));
  const print = el("button", { type: "button", class: "secondary", text: tr("ui.print") });
  print.addEventListener("click", () => window.print());
  parts.push(el("p", { class: "disclaimer" }, view.disclaimer), print);
  target.replaceChildren(...parts);
  target.hidden = false;
  if (scroll) target.scrollIntoView({ behavior: "smooth" });
}

async function runCheck() {
  const details = document.querySelector("details");
  const record = details.open ? safeJson($("json").value) : readForm();
  if (!record) return showErrors(["JSON"]);
  const response = await fetch(`/api/check?lang=${encodeURIComponent(lang)}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(record),
  });
  const body = await response.json();
  if (!response.ok) return showErrors(Array.isArray(body.detail) ? body.detail : [JSON.stringify(body.detail)]);
  showErrors([]);
  $("json").value = JSON.stringify(record, null, 2);
  renderResult(body);
}

async function runExtract() {
  const file = $("photo").files[0];
  if (!file) return;
  $("upload-status").textContent = tr("ui.upload.reading");
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/extract", { method: "POST", body: form });
  const draft = await response.json();
  if (!response.ok) { $("upload-status").textContent = String(draft.detail); return; }
  const record = readForm();
  record.shifts = [...record.shifts, ...draft.shifts.map((s) => ({ ...s, start: s.start.slice(0, 5), end: s.end.slice(0, 5), break_minutes: s.break_minutes ?? 0 }))];
  if (draft.worker_name) record.worker.name = draft.worker_name;
  if (draft.employer_name) record.employer.name = draft.employer_name;
  if (draft.hourly_wage) { record.employment.pay_basis = "hourly"; record.employment.agreed_wage = draft.hourly_wage; }
  else if (draft.monthly_wage) { record.employment.pay_basis = "monthly"; record.employment.agreed_wage = draft.monthly_wage; }
  const dates = record.shifts.map((s) => s.date).sort();
  record.period.start ||= draft.pay_period_start || dates[0] || "";
  record.period.end ||= draft.pay_period_end || dates[dates.length - 1] || "";
  if (draft.gross_pay != null) record.payslips = [{ period: record.period, gross_pay: draft.gross_pay, self_reported: false }];
  fillForm(record);
  $("upload-status").textContent = [tr("ui.upload.done", { shifts: draft.shifts.length }), ...draft.unreadable].join(" ");
}

async function init() {
  meta = await (await fetch("/api/meta")).json();
  $("lang").replaceChildren(...meta.languages.map((l) => el("option", { value: l.code, text: l.name })));
  $("lang").value = lang;
  await loadStrings();
  fillForm({ employment: { relationship: "employee", shift_system: "unknown", special_hours_arrangement: "no",
    responsibility_system: "no", overtime_consent: "unknown", pay_basis: "hourly", week_starts_on: "unknown" }, shifts: [] });
  if (!meta.gemini) { $("extract").disabled = true; $("upload-status").textContent = tr("ui.upload.no_key"); }
  $("lang").addEventListener("change", async (e) => {
    lang = e.target.value; localStorage.setItem("lang", lang); await loadStrings(); window.onLangChange?.();
  });
  $("sample").addEventListener("click", () => fillForm(meta.sample));
  $("add-shift").addEventListener("click", () => $("shift-rows").append(shiftRow()));
  $("check").addEventListener("click", runCheck);
  $("extract").addEventListener("click", runExtract);
  window.initAgent?.();
  $("json").addEventListener("change", () => { const r = safeJson($("json").value); if (r) fillForm(r); });
}

init();
