// DOM helpers. Text is always set via textContent — never innerHTML with data.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const integer = new Intl.NumberFormat("en-US");

export const fmt = {
  money: (value) => currency.format(Number(value ?? 0)),
  int: (value) => integer.format(Number(value ?? 0)),
  label: (value) => String(value ?? "").replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()),
  relative(iso) {
    if (!iso) return "";
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
    return new Date(iso).toLocaleDateString();
  },
  datetime: (iso) => (iso ? new Date(iso).toLocaleString() : "—"),
  // Today's date in the user's time zone (toISOString would give the UTC date).
  today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  },
};

const STATUS_TONES = {
  success: ["COMPLETED", "SUCCEEDED", "APPROVED", "ACTIVE", "WON", "PAID", "DONE", "DELIVERED"],
  warning: ["PENDING", "AWAITING_APPROVAL", "AWAITING_CLIENT", "REVIEW", "PAUSED", "PARTIALLY_PAID"],
  danger: ["FAILED", "REJECTED", "LOST", "CANCELLED", "EXPIRED", "VOID"],
  info: ["RUNNING", "PLANNING", "IN_PROGRESS", "ASSIGNED", "SENT"],
};

const LIVE = new Set(["PENDING", "ASSIGNED", "RUNNING", "PLANNING"]);

export function statusBadge(status) {
  const tone = Object.entries(STATUS_TONES).find(([, list]) => list.includes(status))?.[0];
  const live = LIVE.has(status) ? " badge-live" : "";
  return el("span", { class: `badge${tone ? ` badge-${tone}` : ""}${live}` }, fmt.label(status));
}

export const isActiveStatus = (status) => ["PENDING", "ASSIGNED", "RUNNING"].includes(status);

export function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  if (!host) return;
  const node = el("div", { class: `toast toast-${kind}`, role: "status" }, message);
  host.append(node);
  setTimeout(() => node.remove(), 4500);
}

export function loadingState(text = "Loading…") {
  return el("div", { class: "state" }, el("div", { class: "spinner" }), text);
}

export function errorState(error, onRetry) {
  return el(
    "div",
    { class: "state" },
    el("div", { class: "state-icon" }, "⚠️"),
    el("h2", {}, "Something went wrong"),
    el("p", {}, error?.message ?? "Unexpected error"),
    onRetry ? el("button", { class: "btn", type: "button", onclick: onRetry }, "Try again") : null,
  );
}

// Modal dialog. Returns { close }. Escape and backdrop click close it.
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const previous = document.activeElement;
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    if (previous instanceof HTMLElement) previous.focus();
    onClose?.();
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  const dialog = el(
    "div",
    { class: `modal${wide ? " modal-wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
    el("div", { class: "modal-head" }, el("h2", {}, title), el("button", { class: "btn btn-ghost btn-sm modal-close", type: "button", "aria-label": "Close", onclick: () => close() }, "×")),
    el("div", { class: "modal-body" }, body),
    actions.length ? el("div", { class: "modal-foot" }, actions) : null,
  );
  const backdrop = el("div", { class: "modal-backdrop", onclick: (e) => e.target === backdrop && close() }, dialog);
  document.body.append(backdrop);
  document.addEventListener("keydown", onKey);
  (dialog.querySelector("input, textarea, select") ?? dialog.querySelector(".modal-foot .btn-primary"))?.focus();
  return { close };
}

export function confirmDialog({ title, message, confirmLabel = "Confirm" }) {
  return new Promise((resolve) => {
    let decided = false;
    const done = (value) => {
      decided = true;
      resolve(value);
      handle.close();
    };
    const handle = modal({
      title,
      body: el("p", { class: "muted" }, message),
      actions: [
        el("button", { class: "btn", type: "button", onclick: () => done(false) }, "Cancel"),
        el("button", { class: "btn btn-primary", type: "button", onclick: () => done(true) }, confirmLabel),
      ],
      onClose: () => decided || resolve(false),
    });
  });
}

// Calls fn every `ms` until fn returns false or stop() is called. Never overlaps calls.
export function poll(fn, ms = 1500) {
  let stopped = false;
  let timer = null;
  const tick = async () => {
    if (stopped) return;
    let again = true;
    try {
      again = (await fn()) !== false;
    } catch {
      again = true;
    }
    if (!stopped && again) timer = setTimeout(tick, ms);
  };
  timer = setTimeout(tick, ms);
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}

export const canWrite = (user) => ["FOUNDER", "ADMIN", "STAFF"].includes(user.role);
export const canAdmin = (user) => ["FOUNDER", "ADMIN"].includes(user.role);

export function field(label, control, hint) {
  return el("div", { class: "field" }, el("label", { for: control.id }, label), control, hint ? el("span", { class: "field-hint" }, hint) : null);
}

// Declarative form fields. defs: [{ name, label, type, options, hint, required, full }].
// type: text | email | url | tel | number | date | textarea | select | checkboxes.
// read() returns trimmed values; empty inputs become null.
export function buildForm(prefix, defs, values = {}) {
  const controls = {};
  const rows = [];
  let pair = [];
  const flush = () => {
    if (pair.length) rows.push(pair.length === 2 ? el("div", { class: "form-row" }, pair) : pair[0]);
    pair = [];
  };
  for (const d of defs) {
    const id = `${prefix}-${d.name}`;
    const value = values[d.name];
    let control;
    if (d.type === "textarea") {
      control = el("textarea", { class: "input", id, maxlength: d.max ?? "10000" });
      control.value = value ?? "";
    } else if (d.type === "select") {
      control = el("select", { class: "input", id }, (d.options ?? []).map((o) => {
        const [v, label] = Array.isArray(o) ? o : [o, fmt.label(o)];
        return el("option", { value: String(v), selected: String(value ?? d.default ?? "") === String(v) }, label);
      }));
    } else if (d.type === "checkboxes") {
      const chosen = new Set((value ?? []).map(String));
      control = el("div", { class: "chips", id }, (d.options ?? []).map(([v, label]) =>
        el("label", { class: "check" }, el("input", { type: "checkbox", value: String(v), checked: chosen.has(String(v)) }), label)));
    } else {
      control = el("input", { class: "input", id, type: d.type ?? "text", value: value ?? "", required: d.required, min: d.min, max: d.max, step: d.step });
    }
    controls[d.name] = { control, def: d };
    const wrapped = d.type === "checkboxes"
      ? el("div", { class: "field" }, el("label", {}, d.label), control, d.hint ? el("span", { class: "field-hint" }, d.hint) : null)
      : field(d.label + (d.required ? " *" : ""), control, d.hint);
    if (d.full || d.type === "textarea" || d.type === "checkboxes") {
      flush();
      rows.push(wrapped);
    } else {
      pair.push(wrapped);
      if (pair.length === 2) flush();
    }
  }
  flush();
  const read = () => {
    const out = {};
    for (const [name, { control, def }] of Object.entries(controls)) {
      if (def.type === "checkboxes") {
        out[name] = [...control.querySelectorAll("input:checked")].map((i) => (def.numeric ? Number(i.value) : i.value));
        continue;
      }
      const raw = control.value.trim();
      if (raw === "") out[name] = null;
      else if (def.type === "number") out[name] = def.money ? raw : Number(raw);
      else if (def.numeric) out[name] = Number(raw);
      else out[name] = raw;
    }
    return out;
  };
  return { body: el("div", {}, rows), read, controls };
}

// Modal with a form and a primary action that calls submit(values); errors show inline.
export function formDialog({ title, defs, values, submitLabel = "Save", submit, intro, wide = true }) {
  const error = el("div", { class: "form-error hidden", role: "alert" });
  const form = buildForm(`dlg${Date.now()}`, defs, values);
  const save = el("button", { class: "btn btn-primary", type: "button" }, submitLabel);
  const handle = modal({ title, wide, body: el("div", {}, error, intro ?? null, form.body), actions: [el("button", { class: "btn", type: "button", onclick: () => handle.close() }, "Cancel"), save] });
  save.addEventListener("click", async () => {
    save.disabled = true;
    error.classList.add("hidden");
    try {
      await submit(form.read(), { error, close: handle.close });
    } catch (err) {
      error.replaceChildren(document.createTextNode(err.message));
      if (Array.isArray(err.details)) error.append(el("ul", {}, err.details.map((d) => el("li", {}, `${d.field ?? ""}: ${d.message ?? ""}`))));
      error.classList.remove("hidden");
    } finally {
      save.disabled = false;
      // Errors render at the top of the form; bring them into view on long, scrolled forms.
      if (!error.classList.contains("hidden")) error.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  });
  return handle;
}

export function kvList(rows) {
  return el("dl", { class: "kv" }, rows.filter(Boolean).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v ?? "—")]));
}

export function progressBar(percent) {
  const bar = el("div", { class: "progress", role: "progressbar", "aria-valuenow": String(percent), "aria-valuemin": "0", "aria-valuemax": "100" });
  const fill = el("div", { class: `progress-fill w-${Math.round(Math.max(0, Math.min(100, percent)) / 5) * 5}` });
  bar.append(fill);
  return el("div", { class: "row" }, bar, el("span", { class: "small muted" }, `${percent}%`));
}

export function pager(state, total, pageSize, onChange) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return el("div", { class: "pager" },
    el("span", { class: "small muted" }, `${fmt.int(total)} total · page ${state.page} of ${pages}`),
    el("button", { class: "btn btn-sm", type: "button", disabled: state.page <= 1, onclick: () => { state.page -= 1; onChange(); } }, "Previous"),
    el("button", { class: "btn btn-sm", type: "button", disabled: state.page >= pages, onclick: () => { state.page += 1; onChange(); } }, "Next"));
}
