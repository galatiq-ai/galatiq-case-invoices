import { apiFetch, apiUpload } from "/static/api.js";

const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const resultOut = document.getElementById("result-out");
const approveBtn = document.getElementById("approve-btn");
const refreshBtn = document.getElementById("refresh-btn");
const eventsBody = document.querySelector("#events tbody");

let heldInvoiceId = null;  // set when the shown invoice awaits review, for the approve button

// Statuses at which the pipeline has stopped working the invoice (terminal, or
// parked for a human). Anything else means the background job is still running.
const SETTLED = new Set(["paid", "rejected", "superseded", "failed", "needs_review"]);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;
  resultOut.textContent = `uploading ${file.name}…`;
  try {
    const { data, res } = await apiUpload("/api/invoices", file, "process");
    if (!res.ok) throw new Error(typeof data === "string" ? data : JSON.stringify(data));
    const result = await pollInvoice(data.invoice.id, showResult);
    showResult(result);
    await loadEvents();
  } catch (err) {
    resultOut.textContent = "error: " + err;
  }
});

function showResult(result) {
  resultOut.textContent = formatResult(result);
  heldInvoiceId = result.invoice.status === "needs_review" ? result.invoice.id : null;
  approveBtn.style.display = heldInvoiceId === null ? "none" : "";
}

approveBtn.addEventListener("click", async () => {
  if (heldInvoiceId === null) return;
  approveBtn.disabled = true;
  try {
    const { data, res } = await apiFetch(`/api/invoices/${heldInvoiceId}/approve`, {
      method: "POST", action: "approve",
    });
    if (!res.ok) throw new Error(data?.detail ?? JSON.stringify(data));
    showResult(data);
    await loadEvents();
  } catch (err) {
    resultOut.textContent = "error: " + err;
  } finally {
    approveBtn.disabled = false;
  }
});

async function pollInvoice(id, onUpdate, { interval = 700, timeout = 180000 } = {}) {
  const start = performance.now();
  for (;;) {
    const { data } = await apiFetch(`/api/invoices/${id}`, { action: "poll_invoice" });
    onUpdate?.(data);
    if (SETTLED.has(data.invoice.status) || performance.now() - start > timeout) return data;
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

function formatResult(result) {
  const inv = result.invoice;
  const out = [
    `${inv.invoice_number ?? "(no number)"}   ${inv.status.toUpperCase()}`,
    `vendor: ${inv.vendor_raw ?? "—"}    total: ${inv.stated_total ?? "—"} ${inv.currency ?? ""}`,
  ];
  if (inv.review_category) {
    out.push(`review: ${inv.review_category}${inv.review_level ? ` (${inv.review_level})` : ""}`);
  }
  if (inv.review_summary) out.push(inv.review_summary);

  const items = (result.line_items ?? []).map((li) =>
    `  ${li.item_raw}${li.matched_item ? ` → ${li.matched_item}` : ""}  ×${li.quantity}` +
    `  @${li.unit_price ?? "—"}${li.note ? `  (${li.note})` : ""}`);
  if (items.length) out.push("", ...items);

  const findings = result.findings ?? [];
  if (findings.length) {
    out.push("", "findings:");
    for (const f of findings) {
      const mark = f.severity === "error" ? "✗" : f.severity === "warning" ? "•" : "·";
      out.push(`  ${mark} (${f.source}) ${f.message}`);
    }
  }

  const trace = (result.trace ?? []).map((t) => `${t.stage}/${t.kind}`).join(" → ");
  out.push("", `trace: ${trace}`, `invoice id ${inv.id}`);
  return out.join("\n");
}

refreshBtn.addEventListener("click", loadEvents);

async function loadEvents() {
  const { data } = await apiFetch("/api/events?limit=25", { action: "list_events" });
  eventsBody.replaceChildren(...data.map(rowFor));
}

function rowFor(ev) {
  const tr = document.createElement("tr");
  tr.className = "lvl-" + ev.level;
  const client = ev.data?.client?.kind ?? "—";
  const dbQueries = ev.data?.performance?.db_queries ?? 0;
  const detail = ev.data?.detail ?? "";

  cells(tr, [
    ev.level,
    ev.source,
    client,
    ev.method ?? "",
    ev.path ?? "",
    ev.status_code ?? "",
    ev.duration_ms ?? "",
    dbQueries,
    ev.trace_id,
  ]);

  const detailCell = document.createElement("td");
  detailCell.className = "detail";
  detailCell.textContent = detail;
  tr.appendChild(detailCell);
  return tr;
}

function cells(tr, values) {
  for (const v of values) {
    const td = document.createElement("td");
    td.textContent = String(v);
    tr.appendChild(td);
  }
}

loadEvents();
