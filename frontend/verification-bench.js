import { apiGet } from "./client.js";
import { el, chip } from "./ui.js";

const root = document.getElementById("bench-root");
const lede = document.getElementById("bench-lede");

function pct(passed, total) {
  if (!total) return "N/A";
  return `${Math.round((passed / total) * 100)}%`;
}

function shortDate(iso) {
  if (!iso) return "No completed run yet";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function resultChip(ok) {
  return chip(ok ? "chip-paid" : "chip-critical", ok ? "Safe" : "Unsafe");
}

function findingList(result) {
  const details = result.finding_details || [];
  if (details.length) {
    return details.map((f) => `${f.code}${f.source ? ` (${f.source})` : ""}`);
  }
  return result.actual_findings || [];
}

function metric(label, value, note) {
  return el("div", { class: "bench-metric" },
    el("span", {}, label),
    el("b", { class: "tabular" }, value),
    note ? el("em", {}, note) : null);
}

function caseRow(result) {
  const flagged = findingList(result);
  return el("article", { class: "bench-case" + (result.passed ? "" : " fail") },
    el("div", { class: "bench-case-main" },
      el("div", {},
        el("div", { class: "bench-case-title" },
          el("span", {}, result.file.split("/").pop()),
          resultChip(result.passed)),
        el("p", {}, result.scenario || "No scenario notes."),
        result.model_summary ? el("p", { class: "bench-model" }, result.model_summary) : null)),
    el("div", { class: "bench-case-grid" },
      el("div", {}, el("span", {}, "Risk"), el("b", {}, result.risk || "N/A")),
      el("div", {}, el("span", {}, "Actual"), el("b", {}, result.actual_status || "N/A")),
      el("div", {}, el("span", {}, "Review"), el("b", {}, [result.review_category, result.review_level].filter(Boolean).join(" · ") || "none")),
      el("div", {}, el("span", {}, "Flagged"), el("b", {}, flagged.join(", ") || "none")),
      el("div", {}, el("span", {}, "Watchlist"), el("b", {}, (result.watch_findings || []).join(", ") || "none")),
      el("div", {}, el("span", {}, "Duration"), el("b", { class: "tabular" }, result.duration_s == null ? "N/A" : `${result.duration_s}s`))));
}

function emptyState(data) {
  lede.innerHTML = "<b>No completed verification run yet.</b> Run the bench to populate this page.";
  root.replaceChildren(el("section", { class: "bench-empty card card-pad" },
    el("h2", { class: "section-title" }, "No results found"),
    el("p", { class: "section-note" }, "Run this command from the project root, then refresh the page:"),
    el("code", {}, ".venv/bin/python -m evals.run_evals"),
    el("p", { class: "section-note" }, `Expected output file: ${data.path || "evals/results.json"}`)));
}

function render(data) {
  if (!data.available) return emptyState(data);
  const cases = data.cases || [];
  const passed = data.passed ?? cases.filter((c) => c.passed).length;
  const total = data.total ?? cases.length;
  const failed = Math.max(0, total - passed);

  lede.innerHTML = failed
    ? `<b>${failed} safety invariant${failed === 1 ? "" : "s"} failing.</b> Latest run: ${shortDate(data.ran_at)}.`
    : `<b>All ${total} safety invariants passed.</b> Latest run: ${shortDate(data.ran_at)}.`;

  root.replaceChildren(
    el("section", { class: "bench-summary" },
      metric("Safety rate", pct(passed, total), `${passed}/${total} cases`),
      metric("Unsafe", String(failed), failed ? "needs attention" : "clear"),
      metric("Model", data.model || "N/A", null),
      metric("Run", shortDate(data.ran_at), null)),
    el("section", { class: "bench-cases" }, ...cases.map(caseRow)));
}

async function load() {
  try {
    render(await apiGet("/api/verification-bench", "verification_bench"));
  } catch (err) {
    lede.innerHTML = "<b>Could not load verification results.</b>";
    root.replaceChildren(el("div", { class: "list-foot error" }, err.message));
  }
}

load();
