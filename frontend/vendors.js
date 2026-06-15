import { apiGet } from "./client.js";
import { el, chip } from "./ui.js";

const listEl = document.getElementById("vendor-list");

function row(v) {
  const active = v.status === "active";
  const pos = `${v.open_pos} open purchase order${v.open_pos === 1 ? "" : "s"}`;
  return el("div", { class: "row", style: "cursor:default" },
    el("span", { class: "vendor" },
      el("span", { class: "v-name" }, v.name),
      el("span", { class: "v-no" }, pos)),
    el("span", { class: "status-cell" },
      chip(active ? "chip-paid" : "chip-rejected", active ? "Active" : "Inactive")),
    el("span", { class: "amount tabular" }, v.currency || ""),
    el("span", { class: "when" }, ""));
}

async function load() {
  try {
    const vendors = await apiGet("/api/vendors", "list_vendors");
    const active = vendors.filter((v) => v.status === "active").length;
    listEl.replaceChildren(
      ...vendors.map(row),
      el("div", { class: "list-foot" },
        el("span", {}, `${active} active vendor${active === 1 ? "" : "s"} on file`),
        el("span", {}, "Invoices from anyone not listed route to a person")));
  } catch (err) {
    listEl.replaceChildren(el("div", { class: "list-foot error" },
      el("span", {}, "Couldn't load vendors: " + err.message)));
  }
}

load();
