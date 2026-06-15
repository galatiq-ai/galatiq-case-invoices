// Shared API client. Attaches x-trace-id + x-client to every request so the web
// app stitches into the same wide-event trace the CLI uses.

function traceId() {
  return "trc_" + crypto.randomUUID().replaceAll("-", "").slice(0, 12);
}

function clientHeader(action) {
  return encodeURIComponent(JSON.stringify({
    kind: "web",
    page: location.pathname,
    user_agent: navigator.userAgent,
    action,
  }));
}

function headers(action, json) {
  const h = { "x-trace-id": traceId(), "x-client": clientHeader(action) };
  if (json) h["content-type"] = "application/json";
  return h;
}

async function parse(res) {
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    let detail = data && typeof data === "object" ? data.detail : data;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; "); // FastAPI 422
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export async function apiGet(path, action) {
  return parse(await fetch(path, { headers: headers(action || `GET ${path}`) }));
}

export async function apiPost(path, body, action) {
  return parse(await fetch(path, {
    method: "POST",
    headers: headers(action || `POST ${path}`, true),
    body: body == null ? null : JSON.stringify(body),
  }));
}

export async function apiUpload(path, file, action) {
  const form = new FormData();
  form.append("file", file, file.name);
  return parse(await fetch(path, { method: "POST", headers: headers(action || `upload ${path}`), body: form }));
}
