import { apiGet } from "./client.js";
import { chip, el, money } from "./ui.js";

const listEl = document.getElementById("po-list");

function statusChip(status) {
  return chip(status === "open" ? "chip-paid" : "chip-super", status === "open" ? "Open" : "Closed");
}

function lineRow(line, currency) {
  const remaining = Number(line.qty_remaining ?? 0);
  return el("div", { class: "po-line" },
    el("span", { class: "po-item" }, line.item),
    el("span", { class: "tabular" }, line.qty_ordered),
    el("span", { class: "tabular" }, line.qty_invoiced),
    el("span", { class: "tabular" }, remaining),
    el("span", { class: "tabular" }, money(line.unit_price, currency)));
}

function card(po) {
  const remainingValue = Number(po.total_authorized || 0) - Number(po.total_invoiced || 0);
  return el("article", { class: "po-card" },
    el("div", { class: "po-head" },
      el("div", { class: "po-title" },
        el("span", { class: "po-number" }, po.po_number),
        el("span", { class: "po-vendor" }, po.vendor_name)),
      el("div", { class: "po-status" }, statusChip(po.status))),
    el("div", { class: "po-metrics" },
      el("div", {}, el("span", {}, "Authorized"), el("b", { class: "tabular" }, money(po.total_authorized, po.currency))),
      el("div", {}, el("span", {}, "Invoiced"), el("b", { class: "tabular" }, money(po.total_invoiced, po.currency))),
      el("div", {}, el("span", {}, "Remaining"), el("b", { class: "tabular" }, money(remainingValue, po.currency))),
      el("div", {}, el("span", {}, "Lines"), el("b", { class: "tabular" }, po.line_count))),
    el("div", { class: "po-lines", role: "table", "aria-label": `${po.po_number} lines` },
      el("div", { class: "po-line po-line-head", role: "row" },
        el("span", {}, "Item"),
        el("span", {}, "Ordered"),
        el("span", {}, "Invoiced"),
        el("span", {}, "Remaining"),
        el("span", {}, "Unit price")),
      po.lines.map((line) => lineRow(line, po.currency))));
}

async function load() {
  try {
    const orders = await apiGet("/api/purchase-orders", "list_purchase_orders");
    const open = orders.filter((po) => po.status === "open").length;
    listEl.replaceChildren(
      ...orders.map(card),
      el("div", { class: "list-foot" },
        el("span", {}, `${open} open purchase order${open === 1 ? "" : "s"}`),
        el("span", {}, "Read-only authorization data")));
  } catch (err) {
    listEl.replaceChildren(el("div", { class: "list-foot error" },
      el("span", {}, "Couldn't load purchase orders: " + err.message)));
  }
}

load();
