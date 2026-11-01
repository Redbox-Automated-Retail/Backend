import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import customParseFormat from "dayjs/plugin/customParseFormat";

dayjs.extend(relativeTime);
dayjs.extend(customParseFormat);

export const num = (v: unknown) => Number(v || 0).toLocaleString();
export const ago = (v?: string | null) => (v ? dayjs(v).fromNow() : "never");
export const dateTime = (v?: string | null) => (v ? dayjs(v).format("MMM D, YYYY h:mm A") : "");
export const date = (v?: string | null) => (v ? dayjs(v).format("MMM D, YYYY") : "");
export const short = (h?: string | null) => (h ? String(h).slice(0, 10) : "");
export const bytes = (n: unknown) => {
  let v = Number(n || 0);
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (v >= 1024 && i < 4) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
};
export const duration = (s: unknown) => {
  const t = Number(s || 0);
  const d = Math.floor(t / 86400), h = Math.floor((t % 86400) / 3600), m = Math.floor((t % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
};
/** Redbox's yyyyMMdd[HHmmss] strings -> Date (or null) */
export const fromRedboxDate = (v?: string | null): Date | null => {
  if (!v) return null;
  const d = dayjs(String(v).slice(0, 8), "YYYYMMDD", true);
  return d.isValid() ? d.toDate() : null;
};
export const toIsoDate = (d: Date | null) => (d ? dayjs(d).format("YYYY-MM-DD") : null);
export { dayjs };
