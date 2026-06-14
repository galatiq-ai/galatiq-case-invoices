import { apiFetch } from "/static/api.js";

const helloBtn = document.getElementById("hello-btn");
const helloOut = document.getElementById("hello-out");
const refreshBtn = document.getElementById("refresh-btn");
const eventsBody = document.querySelector("#events tbody");

helloBtn.addEventListener("click", async () => {
  helloBtn.disabled = true;
  helloOut.textContent = "calling…";
  try {
    const { traceId, res, data } = await apiFetch("/api/hello", { method: "POST", action: "hello" });
    helloOut.textContent = JSON.stringify(
      { ...data, eventId: res.headers.get("x-event-id"), sentTraceId: traceId },
      null,
      2,
    );
    await loadEvents();
  } catch (err) {
    helloOut.textContent = "error: " + err;
  } finally {
    helloBtn.disabled = false;
  }
});

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
