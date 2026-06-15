import { apiGet, apiPost } from "./client.js";
import { money, findingTitle, categoryLabel, title, el, chip } from "./ui.js";

const id = new URLSearchParams(location.search).get("id");
const root = document.getElementById("review-root");

const SEV = { error: 0, warning: 1, info: 2 };
const ACTIVE = new Set(["received", "processing"]);
let flash = null; // one-shot message shown after an action

const SVG = {
  back: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>`,
  flag: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>`,
  activity: `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>`,
  caret: `<svg class="caret" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>`,
  edit: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`,
};

function svgEl(markup) {
  const t = document.createElement("template");
  t.innerHTML = markup.trim();
  return t.content.firstChild;
}

function fmtQty(q) {
  if (q == null) return "";
  const n = Number(q);
  return Number.isInteger(n) ? String(n) : String(q);
}

function headChip(inv) {
  if (inv.status === "needs_review") {
    const critical = inv.review_level === "critical" || inv.review_category === "fraud_suspected";
    return chip(critical ? "chip-critical" : "chip-review", "Needs review");
  }
  if (inv.status === "paid" || inv.status === "approved") return chip("chip-paid", "Paid");
  if (inv.status === "rejected") return chip("chip-rejected", "Rejected");
  if (ACTIVE.has(inv.status)) return chip("chip-process", "Processing");
  return chip("chip-super", title(inv.status));
}

// All categories the judge assigned, importance-ordered, each with its 1-10 badge.
function categoriesStrip(categories) {
  const cats = categories || [];
  if (!cats.length) return null;
  const impClass = (n) => (n >= 8 ? "imp-hi" : n >= 5 ? "imp-mid" : "imp-lo");
  return el("div", { class: "cat-strip" },
    el("span", { class: "cat-strip-label" }, "Flagged as"),
    el("div", { class: "cat-chips" }, ...cats.map((c) =>
      el("span", { class: "cat-chip" },
        el("span", { class: "cat-name" }, categoryLabel(c.category)),
        el("span", { class: "cat-imp " + impClass(c.importance), title: "importance 1–10" }, String(c.importance))))));
}

// The deterministic layer and the LLM judge can each raise the same code; collapse
// them so a reviewer never sees two identical rows. Keep the deterministic wording,
// the most-severe level, and note when both layers flagged it.
function dedupeFindings(findings) {
  const m = new Map();
  for (const f of findings) {
    const cur = m.get(f.code);
    if (!cur) {
      m.set(f.code, { code: f.code, severity: f.severity, message: f.message, det: f.source !== "llm", agent: f.source === "llm" });
      continue;
    }
    if (f.source === "llm") cur.agent = true;
    else { cur.det = true; cur.message = f.message; }
    if ((SEV[f.severity] ?? 9) < (SEV[cur.severity] ?? 9)) cur.severity = f.severity;
  }
  return [...m.values()];
}

function findingBy(f) {
  if (f.det && f.agent) return "checks + agent agree";
  if (f.agent) return "raised by the agent";
  return null;
}

function findingsList(findings) {
  const sorted = [...findings].sort((a, b) => (SEV[a.severity] ?? 9) - (SEV[b.severity] ?? 9));
  return el("ul", { class: "findings" }, ...sorted.map((f) => {
    const by = findingBy(f);
    return el("li", { class: "sev-" + (f.severity || "info") },
      el("span", { class: "ico" }, svgEl(SVG.alert)),
      el("span", { class: "f-body" },
        el("span", { class: "f-title" }, findingTitle(f.code)),
        el("span", { class: "f-detail" }, f.message),
        by ? el("span", { class: "f-by" }, by) : null));
  }));
}

function decisionCard(data) {
  const inv = data.invoice;
  const held = inv.status === "needs_review";
  const flagged = inv.vendor_id == null && inv.vendor_raw;

  const hero = el("div", { class: "hero" },
    el("span", { class: "h-label" }, held ? "Amount requested" : "Amount"),
    el("span", { class: "h-amount tabular" }, money(inv.stated_total), el("span", { class: "cur" }, inv.currency || "USD")),
    flagged ? el("span", { class: "h-flag" }, svgEl(SVG.flag), "Vendor is not in our master file") : null);

  const findings = dedupeFindings(data.findings);
  const body = el("div", { class: "card-pad" });
  const strip = categoriesStrip(data.categories);
  if (strip) body.append(strip);
  if (findings.length) {
    body.append(
      el("h2", { class: "section-title" }, held ? "Why it is held" : "What the checks found"),
      el("p", { class: "section-note" }, `${findings.length} signal${findings.length === 1 ? "" : "s"} from the deterministic checks and the agent.`),
      findingsList(findings));
  } else {
    body.append(
      el("h2", { class: "section-title" }, "Assessment"),
      el("p", { class: "section-note" }, "No issues found. Cleared to pay touchless."));
  }

  if (inv.recommendation || inv.review_summary) {
    const lead = inv.recommendation === "hold" ? "Recommend HOLD." : inv.recommendation === "pay" ? "Recommend pay." : "";
    body.append(el("div", { class: "reco" },
      el("span", { class: "badge" }, "Agent"),
      el("span", { class: "reco-text" }, lead ? el("b", {}, lead) : null, inv.review_summary ? " " + inv.review_summary : "")));
    if (data.trace.some((t) => t.kind === "human_edit"))
      body.append(el("p", { class: "stale-note" },
        "Assessed before your correction. The hard checks above were re-run on your edit, but the agent's verdict and alarm level predate it."));
  }

  return el("section", { class: "card reveal", style: "animation-delay:.2s" }, hero, body);
}

function traceSteps(trace) {
  const out = [];
  let extracted = false;
  for (const t of trace) {
    const p = t.payload || {};
    const ms = p._meta ? p._meta.duration_ms : null;
    if (t.kind === "route") out.push({ name: "Ingest", note: p.kind || p.summary || "received", ms });
    else if (t.stage === "extract" && t.kind === "llm_call" && !extracted) { extracted = true; out.push({ name: "Extract", note: "read the document", ms }); }
    else if (t.kind === "check") out.push({ name: "Validate", note: p.summary || "checked vendors + POs", ms });
    else if (t.kind === "verdict") out.push({ name: "Judge", note: p.category ? `${p.recommendation} · ${categoryLabel(p.category)}` : (p.recommendation || "assessed"), ms });
    else if (t.kind === "gate") out.push({ name: "Gate", note: p.outcome === "needs_review" ? "routed to a human" : (p.outcome || "decided"), hold: p.outcome === "needs_review", ms });
    else if (t.kind === "payment") out.push({ name: "Pay", note: "payment sent", ms });
    else if (t.kind === "human_approve") out.push({ name: "Approved by reviewer", note: "paid", ms: null });
    else if (t.kind === "human_reject") out.push({ name: "Rejected by reviewer", note: p.reason || "declined", hold: true, ms: null });
    else if (t.kind === "note") out.push({ name: "Reviewer note", note: p.note || "", ms: null });
  }
  return out;
}

function traceDisclosure(trace) {
  const steps = traceSteps(trace);
  if (!steps.length) return null;
  return el("details", { class: "disclosure reveal", style: "animation-delay:.32s" },
    el("summary", {}, svgEl(SVG.activity), "View processing trace", svgEl(SVG.caret)),
    el("div", { class: "trace" }, ...steps.map((s) =>
      el("div", { class: "trace-step" + (s.hold ? " hold" : "") },
        el("span", { class: "node" }),
        el("span", { class: "t-name" }, s.name, s.note ? el("small", {}, s.note) : null),
        s.ms != null ? el("span", { class: "t-ms tabular" }, `${Number(s.ms).toLocaleString()} ms`) : el("span", { class: "t-ms" }, "")))));
}

function documentCard(inv) {
  const fmt = (inv.source_format || "").toLowerCase();
  const name = (inv.source_path || "").split("/").pop() || "document";
  const url = `/api/invoices/${inv.id}/source`;

  const card = el("section", { class: "card card-pad reveal doc-card", style: "animation-delay:.24s" },
    el("h2", { class: "section-title" }, "Original document"),
    el("div", { class: "doc-head" },
      el("span", { class: "doc-name" }, name),
      el("span", { class: "doc-fmt" }, fmt.toUpperCase() || "FILE"),
      el("a", { class: "doc-open", href: url, target: "_blank", rel: "noopener" }, "Open original ↗")));

  if (fmt === "pdf") {
    card.append(el("embed", { class: "doc-frame", type: "application/pdf", src: url, "aria-label": "Original invoice PDF" }));
  } else {
    const pre = el("pre", { class: "doc-text", tabindex: "0" }, "Loading…");
    card.append(pre);
    apiGet(url, "view_source")
      .then((body) => { pre.textContent = typeof body === "string" ? body : JSON.stringify(body, null, 2); })
      .catch((err) => { pre.textContent = "Couldn't load the document: " + err.message; });
  }
  return card;
}

function essentialsCard(data) {
  const inv = data.invoice;
  const facts = el("ul", { class: "facts", style: "margin-top:14px" },
    el("li", {}, el("span", { class: "k" }, "Due date"),
      el("span", { class: "v" + (inv.due_date_raw ? " warn" : "") }, inv.due_date_raw ? `${inv.due_date_raw} · unparseable` : (inv.due_date || "N/A"))),
    el("li", {}, el("span", { class: "k" }, "Terms"), el("span", { class: "v" }, inv.payment_terms || "N/A")),
    el("li", {}, el("span", { class: "k" }, "Line items"), el("span", { class: "v" }, String(data.line_items.length))),
    ...data.line_items.map((li) =>
      el("li", {}, el("span", { class: "k" }, `${li.item_raw} × ${fmtQty(li.quantity)}`),
        el("span", { class: "v tabular" }, money(li.unit_price)))));

  const card = el("section", { class: "card card-pad reveal", style: "animation-delay:.26s" },
    el("h2", { class: "section-title" }, "The essentials"), facts);

  if (inv.status === "needs_review") card.append(correctionForm(data), actions(inv));
  else card.append(resolved(inv));
  return card;
}

// The approve friction scales with the judge's alarm level (review.py): a low/medium
// hold is a routine sign-off, high is overriding a real warning, critical demands an
// explicit acknowledgment. Copy, colour, and the ack-gate all key off the level.
function guardConfig(inv) {
  const amt = money(inv.stated_total);
  const cat = (categoryLabel(inv.review_category) || "review").toLowerCase();
  const unknownVendor = inv.vendor_raw && inv.vendor_id == null;
  if (inv.review_level === "critical") {
    return {
      tone: "danger", requireAck: true,
      ackLabel: `I understand this is flagged as ${cat}`,
      note: `${unknownVendor ? `Approving pays ${amt} to ${inv.vendor_raw}, which isn't in your vendor master. ` : `Approving pays ${amt}. `}The agent strongly advises against it.`,
      armLabel: `Confirm pay ${amt} anyway`,
    };
  }
  if (inv.review_level === "high") {
    return {
      tone: "warn", requireAck: false,
      note: `The agent held this for ${cat} and recommends against paying. Approving overrides that and pays ${amt}.`,
      armLabel: `Confirm override and pay ${amt}`,
    };
  }
  return {
    tone: "calm", requireAck: false,
    note: `Held for ${cat}. Review the details, then sign off to pay ${amt}.`,
    armLabel: `Confirm pay ${amt}`,
  };
}

function actions(inv) {
  const cfg = guardConfig(inv);
  const msg = el("p", { class: "action-msg", id: "action-msg", role: "status", "aria-live": "polite" }, flash ? flash.text : "");
  if (flash) { msg.classList.add(flash.ok ? "ok" : "error"); flash = null; }

  const reject = el("button", { class: "btn btn-lg btn-reject", type: "button" }, "Reject & log");
  const approve = el("button", { class: `btn btn-lg btn-approve g-${cfg.tone}`, id: "approveBtn", type: "button" }, "Approve & Pay");
  const note = el("textarea", { id: "note", placeholder: "Add a note for the audit trail…" });

  const noteVal = () => note.value.trim();
  const showMsg = (text, err) => { msg.textContent = text; msg.className = "action-msg " + (err ? "error" : "ok"); };
  const busy = (on) => [reject, approve].forEach((b) => { b.disabled = on; });

  async function act(kind, body, okText) {
    busy(true);
    try {
      flash = { ok: true, text: okText };
      render(await apiPost(`/api/invoices/${inv.id}/${kind}`, body, kind));
    } catch (err) { busy(false); flash = null; showMsg(err.message, true); }
  }

  reject.onclick = () => {
    const reason = noteVal();
    if (!reason) { showMsg("Add a reason in the note before rejecting.", true); note.focus(); return; }
    act("reject", { reason }, "Rejected and logged.");
  };
  let armed = false, t;
  approve.setAttribute("aria-pressed", "false");
  const disarm = () => { armed = false; approve.setAttribute("aria-pressed", "false"); approve.textContent = "Approve & Pay"; approve.classList.remove("armed"); };

  // critical holds gate the button behind an explicit acknowledgment
  let ackRow = null;
  if (cfg.requireAck) {
    const ack = el("input", { type: "checkbox", id: "ack" });
    approve.disabled = true;
    ack.addEventListener("change", () => { approve.disabled = !ack.checked; if (!ack.checked) disarm(); });
    ackRow = el("label", { class: "ack", for: "ack" }, ack, el("span", {}, cfg.ackLabel));
  }

  approve.onclick = () => {
    if (!armed) {
      armed = true;
      approve.setAttribute("aria-pressed", "true");
      approve.textContent = cfg.armLabel;
      approve.classList.add("armed");
      showMsg(`${cfg.armLabel}. Click again, or wait to cancel.`);
      clearTimeout(t);
      t = setTimeout(disarm, 4500);
      return;
    }
    act("approve", { note: noteVal() || undefined }, "Payment sent.");
  };

  return el("div", { class: "actions" },
    el("div", { class: "action-row" }, reject),
    el("div", { class: "approve-guard g-" + cfg.tone }, ackRow, approve,
      el("span", { class: "guard-note" }, cfg.note)),
    el("div", { class: "note-field" }, el("label", { for: "note" }, "Reviewer note"), note),
    msg);
}

// Inline correction of misread fields. Sends only changed fields to /correct; the
// backend logs each old -> new on the trail and re-runs the deterministic checks.
function correctionForm(data) {
  const inv = data.invoice;
  const fields = [
    ["Vendor", "vendor_raw", "text"],
    ["Invoice #", "invoice_number", "text"],
    ["Amount", "stated_total", "number"],
    ["Currency", "currency", "text"],
    ["Due date", "due_date", "text"],
    ["Terms", "payment_terms", "text"],
  ].map(([label, key, type]) => {
    const input = el("input", { type, class: "corr-input", value: inv[key] == null ? "" : inv[key] });
    return { key, type, input, row: el("label", { class: "corr-field" }, el("span", { class: "corr-label" }, label), input) };
  });

  const lines = data.line_items.map((li) => ({
    id: li.id, orig: li,
    item: el("input", { type: "text", class: "corr-input", value: li.item_raw ?? "" }),
    qty: el("input", { type: "number", class: "corr-input corr-num", value: li.quantity ?? "" }),
    price: el("input", { type: "number", class: "corr-input corr-num", value: li.unit_price ?? "" }),
  }));

  const msg = el("p", { class: "action-msg", role: "status", "aria-live": "polite" });
  const save = el("button", { class: "btn btn-lg btn-reject", type: "button" }, "Save corrections");
  const cancel = el("button", { class: "btn btn-lg btn-info", type: "button" }, "Cancel");

  const norm = (v) => (v === "" || v == null ? null : v);
  save.onclick = async () => {
    const body = {};
    for (const f of fields) {
      let val = norm(f.input.value.trim());
      if (f.type === "number" && val != null) val = Number(val);
      const orig = norm(inv[f.key]);
      const same = f.type === "number" ? Number(orig) === val : orig === val;
      if (!same) body[f.key] = val;
    }
    const liEdits = [];
    for (const r of lines) {
      const edit = { id: r.id };
      let changed = false;
      if (r.item.value !== (r.orig.item_raw ?? "")) { edit.item_raw = r.item.value; changed = true; }
      const qn = norm(r.qty.value) == null ? null : Number(r.qty.value);
      if (qn !== (r.orig.quantity ?? null)) { edit.quantity = qn; changed = true; }
      const pn = norm(r.price.value) == null ? null : Number(r.price.value);
      if (pn !== (r.orig.unit_price ?? null)) { edit.unit_price = pn; changed = true; }
      if (changed) liEdits.push(edit);
    }
    if (liEdits.length) body.line_items = liEdits;
    if (!Object.keys(body).length) { msg.textContent = "No changes to save."; msg.className = "action-msg"; return; }
    save.disabled = cancel.disabled = true;
    try {
      flash = { ok: true, text: "Corrections saved. The hard checks were re-run." };
      render(await apiPost(`/api/invoices/${inv.id}/correct`, body, "correct"));
    } catch (err) { save.disabled = cancel.disabled = false; flash = null; msg.textContent = err.message; msg.className = "action-msg error"; }
  };

  const details = el("details", { class: "disclosure corr-disclosure" },
    el("summary", {}, svgEl(SVG.edit), "Correct a misread field", svgEl(SVG.caret)),
    el("div", { class: "corr-body" },
      el("p", { class: "corr-help" }, "Fix anything the extractor got wrong. Every change is logged to the audit trail, and the deterministic checks re-run on the corrected data."),
      el("div", { class: "corr-grid" }, ...fields.map((f) => f.row)),
      lines.length ? el("div", { class: "corr-li-head" }, el("span", {}, "Item"), el("span", {}, "Qty"), el("span", {}, "Unit price")) : null,
      ...lines.map((r) => el("div", { class: "corr-li" }, r.item, r.qty, r.price)),
      el("div", { class: "action-row", style: "margin-top:14px" }, save, cancel),
      msg));
  cancel.onclick = () => { details.open = false; };
  return details;
}

function resolved(inv) {
  const map = {
    paid: ["", "Paid. This invoice has been disbursed."],
    approved: ["", "Approved. Payment in flight."],
    rejected: ["rejected", "Rejected. Declined and logged."],
    superseded: ["rejected", "Superseded. An exact duplicate of an earlier invoice."],
    failed: ["rejected", "Processing failed. See the trace."],
  }[inv.status];
  const banner = el("div", { class: "resolved " + (map ? map[0] : "") }, map ? map[1] : title(inv.status));
  const wrap = el("div", { class: "actions" }, banner);
  if (flash) { wrap.append(el("p", { class: "action-msg ok" }, flash.text)); flash = null; }
  return wrap;
}

function render(data) {
  const inv = data.invoice;
  root.replaceChildren(
    el("a", { class: "back reveal", href: "index.html", style: "animation-delay:.05s" }, svgEl(SVG.back), "Back to inbox"),
    el("header", { class: "r-head reveal", style: "animation-delay:.12s" },
      el("h1", { class: "inv-no tabular", style: "margin:0; font-weight:500" }, inv.invoice_number || `#${inv.id}`),
      el("span", { class: "sep" }, "·"),
      el("span", { class: "vendor-name" }, inv.vendor_raw || "(no vendor)"),
      headChip(inv)),
    el("div", { class: "r-grid" },
      el("div", {}, decisionCard(data), documentCard(inv), traceDisclosure(data.trace)),
      el("aside", {}, essentialsCard(data))));

  if (ACTIVE.has(inv.status)) setTimeout(load, 2000); // still processing - keep refreshing
}

async function load() {
  if (!id) { root.replaceChildren(el("p", { class: "loading" }, "No invoice selected.")); return; }
  try {
    render(await apiGet(`/api/invoices/${id}`, "get_invoice"));
  } catch (err) {
    root.replaceChildren(el("p", { class: "loading" }, "Couldn't load this invoice: " + err.message));
  }
}

load();
