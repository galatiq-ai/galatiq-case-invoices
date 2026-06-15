// Shared rendering helpers and the backend vocabulary, mapped to display labels.
// The look comes entirely from styles.css; these build the same markup the
// static mockup used, from live data.

export function money(amount, currency) {
  if (amount == null || amount === "") return "—";
  const num = Number(amount);
  if (!Number.isFinite(num)) return "—";
  const sign = num < 0 ? "-" : "";
  const n = Math.abs(num).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency && currency !== "USD" ? `${sign}${currency} ${n}` : `${sign}$${n}`;
}

export function relativeTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso.replace(" ", "T") + "Z"); // sqlite datetime('now') is UTC
  if (Number.isNaN(t)) return "";
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

export function title(s) {
  return String(s || "").replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const CATEGORY_LABEL = {
  fraud_suspected: "Fraud suspected", over_budget: "Over budget", unknown_vendor: "Unknown vendor",
  missing_po: "Missing PO", data_integrity: "Data integrity", oversize: "Oversize",
  legibility: "Legibility", duplicate: "Duplicate",
};
export function categoryLabel(c) {
  return c ? (CATEGORY_LABEL[c] || title(c)) : "";
}

const FINDING_TITLE = {
  unknown_vendor: "Unknown vendor", vendor_inactive: "Vendor inactive", no_po: "No purchase order",
  po_not_open: "Purchase order closed", po_vendor_mismatch: "PO / vendor mismatch",
  item_not_on_po: "Unauthorized item", qty_over_authorized: "Quantity over authorized",
  price_mismatch: "Price mismatch", currency_mismatch: "Currency mismatch",
  arithmetic_mismatch: "Arithmetic mismatch", negative_quantity: "Negative quantity",
  negative_price: "Negative price", missing_field: "Missing field", due_date_invalid: "Invalid due date",
  oversize: "Over the auto-pay ceiling", duplicate: "Duplicate invoice", illegible: "Illegible document",
};
export function findingTitle(code) {
  return FINDING_TITLE[code] || title(code);
}

// Status (+ review category/level) -> the chip + optional reason tag shown in a row.
export function statusChip(inv) {
  const { status, review_category, review_level } = inv;
  if (status === "needs_review") {
    const critical = review_level === "critical" || review_category === "fraud_suspected";
    if (critical) {
      return {
        rowClass: "critical",
        chip: { cls: "chip-critical", label: categoryLabel(review_category) || "Critical" },
        tag: { cls: "tag crit", label: "needs review · critical" },
      };
    }
    return {
      rowClass: "",
      chip: { cls: "chip-review", label: "Needs review" },
      tag: review_category ? { cls: "tag", label: categoryLabel(review_category).toLowerCase() } : null,
    };
  }
  const simple = {
    processing: ["chip-process", "Processing"],
    received: ["chip-process", "Received"],
    approved: ["chip-paid", "Approved"],
    paid: ["chip-paid", "Paid"],
    rejected: ["chip-rejected", "Rejected"],
    superseded: ["chip-super", "Superseded"],
    failed: ["chip-failed", "Failed"],
  }[status] || ["chip-process", title(status)];
  const tag = status === "rejected" && review_category
    ? { cls: "tag", label: categoryLabel(review_category).toLowerCase() }
    : status === "superseded" ? { cls: "tag", label: "duplicate" } : null;
  return { rowClass: "", chip: { cls: simple[0], label: simple[1] }, tag };
}

// Tiny DOM builder. Text children become text nodes (escaped); { html } sets
// innerHTML for trusted static SVG markup only — never user/LLM data.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) { if (typeof v === "function") node.addEventListener(k.slice(2), v); }
    else if ((k === "href" || k === "src") && /^\s*(?:javascript|data):/i.test(String(v))) continue; // block unsafe URL schemes
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function chip(cls, label) {
  return el("span", { class: "chip " + cls }, el("span", { class: "seed" }), label);
}
