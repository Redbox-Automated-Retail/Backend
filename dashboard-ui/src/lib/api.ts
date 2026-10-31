// JSON client for the dashboard API (redbox_backend/dashboard). Every changing request carries X-Requested-With.
export class ApiError extends Error {
  constructor(public status: number, message: string, public data?: unknown) {
    super(message);
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const init: RequestInit = { method: opts.method || "GET", headers: { "X-Requested-With": "rb-admin" }, credentials: "same-origin", signal: opts.signal };
  if (opts.form) {
    init.body = opts.form;
    init.method = opts.method || "POST";
  } else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.method = opts.method || "POST";
  }
  const r = await fetch("/api" + path, init);
  const text = await r.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    if (r.status === 401 && path !== "/login") window.dispatchEvent(new Event("rb:logged-out"));
    const msg = (data && (typeof data.detail === "string" ? data.detail : data.error)) || r.statusText || `HTTP ${r.status}`;
    throw new ApiError(r.status, msg, data);
  }
  return data as T;
}

/** query string from an object, leaving out empty values */
export function qs(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? "?" + s : "";
}

/** A page of a server-side list: rows plus the total that match the filters. */
export type Page<T> = { rows: T[]; total: number };
export type ListParams = { limit: number; offset: number; sort?: string; dir?: "asc" | "desc"; [k: string]: unknown };
