// AI CEO chat: multiple persistent conversations; each request shows its live delegation tree.
import { api, showFormError } from "../api.js";
import { canWrite, confirmDialog, el, errorState, fmt, isActiveStatus, loadingState, poll, toast } from "../ui.js";
import { taskTree } from "./task-view.js";

const EXAMPLES = [
  "Plan our HVAC lead-generation launch: research the target market strategy, prepare a cold email sequence, and create a landing page.",
  "Prepare a proposal for a plumbing company.",
  "Show me the agency's financial position.",
  "Give me the status of all active projects.",
  "Delegate this task to the Sales Agent: build a qualification checklist for roofing companies.",
];

function treeIsActive(task) {
  return isActiveStatus(task.status) || (task.children ?? []).some(treeIsActive);
}

export async function renderCeo(ctx) {
  const { view, user, system } = ctx;
  const conversationId = ctx.params[0] ? Number(ctx.params[0]) : null;
  let stopPolling = null;

  const listEl = el("ul", { class: "convo-list" });
  const sidebar = el(
    "section",
    { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "Conversations"), canWrite(user) ? el("a", { class: "btn btn-sm", href: "#/ceo" }, "New") : null),
    listEl,
  );
  const thread = el("div", { class: "thread", "aria-live": "polite" });
  const title = el("h2", {}, "New conversation");
  const deleteBtn = el("button", { class: "btn btn-sm btn-ghost ml-auto hidden", type: "button" }, "Delete");
  const error = el("div", { class: "form-error hidden", role: "alert" });
  const input = el("textarea", { class: "input", id: "ceo-input", rows: "2", maxlength: "8000", placeholder: "Ask the AI CEO… (Enter to send, Shift+Enter for a new line)", "aria-label": "Message to the AI CEO" });
  const send = el("button", { class: "btn btn-primary", type: "submit" }, "Send");
  const composer = canWrite(user)
    ? el("form", { class: "composer" }, input, send)
    : el("div", { class: "composer muted small" }, "Your role is read-only, so you can read conversations but not send instructions.");
  const chat = el(
    "section",
    { class: "card chat" },
    el("div", { class: "chat-head" }, title, el("span", { class: "badge badge-primary" }, `LLM: ${system.llm.provider}`), deleteBtn),
    thread,
    error,
    composer,
  );

  view.replaceChildren(el("div", { class: "chat-layout" }, sidebar, chat));

  async function loadList() {
    try {
      const { items } = await api("/ceo/conversations");
      listEl.replaceChildren(
        ...(items.length
          ? items.map((c) =>
              el("li", {}, el("a", { href: `#/ceo/${c.id}`, class: c.id === conversationId ? "active" : "" }, el("span", { class: "convo-title" }, c.title), el("span", { class: "convo-sub" }, `${c.message_count} messages · ${fmt.relative(c.updated_at)}`))),
            )
          : [el("li", { class: "empty-inline card-pad" }, "No conversations yet.")]),
      );
    } catch (err) {
      listEl.replaceChildren(el("li", { class: "empty-inline card-pad" }, err.message));
    }
  }

  function emptyState() {
    return el(
      "div",
      { class: "chat-empty" },
      el("h2", {}, "What should the team work on?"),
      el("p", {}, `The AI CEO plans your request and delegates it to specialist agents. Drafts only: anything external (sending, publishing, spending) becomes an approval request. ${system.llm.provider === "mock" ? "The offline mock provider is active, so answers are templated examples." : ""}`),
      canWrite(user)
        ? el("div", { class: "chips" }, EXAMPLES.map((text) => el("button", { class: "chip", type: "button", onclick: () => { input.value = text; input.focus(); } }, text)))
        : null,
    );
  }

  function messageView(m) {
    const isUser = m.role === "user";
    return el(
      "div",
      { class: `msg ${isUser ? "msg-user" : "msg-assistant"}` },
      el("div", { class: "bubble" }, m.content),
      m.task ? el("div", { class: "task-card" }, taskTree(m.task)) : null,
      el("div", { class: "msg-meta" }, isUser ? `You · ${fmt.datetime(m.created_at)}` : `AI CEO · ${fmt.datetime(m.created_at)}`, m.task_id && !isUser ? el("span", {}, " · ", el("a", { href: `#/tasks/${m.task_id}` }, "View task")) : null),
    );
  }

  async function loadConversation() {
    if (!conversationId) {
      thread.replaceChildren(emptyState());
      return false;
    }
    const convo = await api(`/ceo/conversations/${conversationId}`);
    title.textContent = convo.title;
    deleteBtn.classList.toggle("hidden", !canWrite(user));
    const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
    thread.replaceChildren(...convo.messages.map(messageView));
    if (nearBottom || !thread.dataset.loaded) thread.scrollTop = thread.scrollHeight;
    thread.dataset.loaded = "1";
    return convo.messages.some((m) => m.task && treeIsActive(m.task));
  }

  function watch() {
    stopPolling?.();
    stopPolling = poll(async () => {
      const active = await loadConversation();
      if (!active) loadList();
      return active;
    }, 1200);
  }

  deleteBtn.addEventListener("click", async () => {
    if (!(await confirmDialog({ title: "Delete conversation?", message: "The conversation is hidden from your list. Its tasks and execution history are kept.", confirmLabel: "Delete" }))) return;
    try {
      await api(`/ceo/conversations/${conversationId}`, { method: "DELETE" });
      toast("Conversation deleted", "success");
      window.location.hash = "#/ceo";
    } catch (err) {
      toast(err.message, "error");
    }
  });

  if (composer.tagName === "FORM") {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        composer.requestSubmit();
      }
    });
    composer.addEventListener("submit", async (event) => {
      event.preventDefault();
      const content = input.value.trim();
      if (!content) return;
      error.classList.add("hidden");
      send.disabled = true;
      try {
        const created = await api("/ceo/messages", { method: "POST", body: { content, conversation_id: conversationId } });
        input.value = "";
        if (created.conversation_id !== conversationId) {
          window.location.hash = `#/ceo/${created.conversation_id}`;
          return;
        }
        await loadConversation();
        thread.scrollTop = thread.scrollHeight;
        watch();
      } catch (err) {
        showFormError(error, err);
      } finally {
        send.disabled = false;
      }
    });
  }

  thread.replaceChildren(loadingState());
  loadList();
  try {
    if (await loadConversation()) watch();
  } catch (err) {
    thread.replaceChildren(errorState(err, () => renderCeo(ctx)));
  }
  input.focus?.();
  return () => stopPolling?.();
}
