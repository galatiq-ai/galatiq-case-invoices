import { apiGet } from "./client.js";
import { money, relativeTime, statusChip, el, chip } from "./ui.js";

const listEl = document.getElementById("list");
const ledeEl = document.getElementById("lede");

const ACTIVE = new Set(["received", "processing"]); // still moving — poll while any are
let timer = null;

function row(inv) {
  const s = statusChip(inv);
  return el("a", { class: ("row " + s.rowClass).trim(), href: `review.html?id=${inv.id}` },
    el("span", { class: "vendor" },
      el("span", { class: "v-name" }, inv.vendor_raw || "(no vendor)"),
      el("span", { class: "v-no tabular" }, inv.invoice_number || `#${inv.id}`)),
    el("span", { class: "status-cell" },
      chip(s.chip.cls, s.chip.label),
      s.tag ? el("span", { class: s.tag.cls }, s.tag.label) : null),
    el("span", { class: "amount tabular" }, money(inv.stated_total, inv.currency)),
    el("span", { class: "when" }, relativeTime(inv.created_at)));
}

function render(invoices) {
  const held = invoices.filter((i) => i.status === "needs_review").length;
  if (ledeEl) {
    ledeEl.innerHTML = held
      ? `<b>${held} invoice${held === 1 ? "" : "s"} need${held === 1 ? "s" : ""} your review.</b> The agents have cleared the rest.`
      : `<b>Nothing waiting on you.</b> The agents have cleared every invoice.`;
  }
  if (!invoices.length) {
    listEl.replaceChildren(el("div", { class: "list-foot empty" },
      el("span", {}, "No invoices yet — upload one to get started.")));
    return;
  }
  listEl.replaceChildren(
    ...invoices.map(row),
    el("div", { class: "list-foot" }, el("span", {}, "Everything else has been paid touchless.")));
}

async function load() {
  try {
    const invoices = await apiGet("/api/invoices", "list_invoices");
    render(invoices);
    const stillMoving = invoices.some((i) => ACTIVE.has(i.status));
    clearTimeout(timer);
    if (stillMoving) timer = setTimeout(load, 2500); // watch it move through the queue
  } catch (err) {
    listEl.replaceChildren(el("div", { class: "list-foot error" },
      el("span", {}, "Couldn't load invoices: " + err.message)));
  }
}

load();
