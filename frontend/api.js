// Shared API client: attaches x-trace-id + x-client to every request.

export function makeTraceId() {
  return "trc_" + crypto.randomUUID().replaceAll("-", "").slice(0, 12);
}

function clientHeader(action) {
  const ctx = {
    kind: "web",
    page: location.pathname,
    user_agent: navigator.userAgent,
    action,
  };
  return encodeURIComponent(JSON.stringify(ctx));
}

export async function apiFetch(path, { method = "GET", body = null, action } = {}) {
  const traceId = makeTraceId();

  const headers = {
    "x-trace-id": traceId,
    "x-client": clientHeader(action ?? `${method} ${path}`),
  };

  const init = { method, headers };
  if (body !== null) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  const res = await fetch(path, init);
  const data = (res.headers.get("content-type") || "").includes("application/json")
    ? await res.json()
    : null;
  return { traceId, res, data };
}

export async function apiUpload(path, file, action) {
  const traceId = makeTraceId();
  const form = new FormData();
  form.append("file", file, file.name);

  const res = await fetch(path, {
    method: "POST",
    headers: { "x-trace-id": traceId, "x-client": clientHeader(action ?? `upload ${path}`) },
    body: form,
  });
  const data = (res.headers.get("content-type") || "").includes("application/json")
    ? await res.json()
    : null;
  return { traceId, res, data };
}
