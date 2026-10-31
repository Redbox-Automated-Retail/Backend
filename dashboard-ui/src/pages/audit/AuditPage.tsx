import { useState } from "react";
import { Code, Group, SegmentedControl, Stack, Text } from "@mantine/core";
import { useSearchParams } from "react-router-dom";
import { ServerTable, useUrlSort, EmptyState, type Column } from "@/components/DataTable";
import { PageHeader, Status, Toolbar, UrlSearch, useUrlParam } from "@/components/ui";
import { api, qs, type Page } from "@/lib/api";
import { dateTime, num } from "@/lib/format";
import { EventDrawer, type EventRow } from "../logs/EventDrawer";
import { UrlDateRange, rangeParams } from "../logs/filters";

type P = { user?: string; username?: string; by?: string; role?: string; status?: number; name?: string } | null;
const payload = (r: EventRow) => (r.payload || {}) as NonNullable<P>;

function Result({ r }: { r: EventRow }) {
  const p = payload(r);
  if (r.source === "audit") {
    const s = Number(p.status);
    return s && s < 400 ? <Status kind="ok">{s}</Status> : <Status kind="bad">{p.status ?? "?"}</Status>;
  }
  if (r.kind === "login-failed") return <Status kind="bad">failed</Status>;
  if (r.kind === "login") return <Status kind="ok">signed in</Status>;
  return null;
}

export function AuditPage() {
  const [sp] = useSearchParams();
  const [src, setSrc] = useUrlParam("source");
  const source = src === "dashboard" ? "dashboard" : "audit";
  const [sort, setSort] = useUrlSort({ sort: "created_at", dir: "desc" });
  const [open, setOpen] = useState<EventRow | null>(null);
  const f = { source, q: sp.get("q") || undefined, ...rangeParams(sp.get("since"), sp.get("until")) };

  const columns: Column<EventRow>[] = [
    { id: "created_at", header: "When", cell: (r) => <Text size="sm" style={{ whiteSpace: "nowrap" }}>{dateTime(r.created_at)}</Text>, sort: "created_at", width: 200 },
    { id: "who", header: "Who", width: 220, cell: (r) => {
      const p = payload(r);
      return <Group gap={6} wrap="nowrap"><Text size="sm" fw={700}>{p.user || p.username || p.by || "-"}</Text>{p.role && <Text size="xs" c="dark.1" tt="capitalize">{p.role}</Text>}</Group>;
    } },
    { id: "kind", header: "What", sort: "kind", cell: (r) => (
      <Group gap={8} wrap="nowrap"><Code>{r.kind}</Code>{r.source === "dashboard" && p0name(r) && <Text size="sm">{p0name(r)}</Text>}</Group>
    ) },
    { id: "result", header: "Result", align: "right", width: 130, cell: (r) => <Result r={r} /> },
  ];

  return (
    <>
      <PageHeader title="Audit log" description="Every change made in the dashboard (who, what, and the answer), and sign-ins" />
      <Toolbar>
        <Stack gap={4}>
          <Text size="sm" fw={500}>Show</Text>
          <SegmentedControl value={source} onChange={(v) => setSrc(v === "audit" ? null : v)}
            data={[{ value: "audit", label: "Changes" }, { value: "dashboard", label: "Sign-ins and dashboard events" }]} />
        </Stack>
        <UrlSearch placeholder={source === "audit" ? "User, method or path" : "User name or event"} />
        <UrlDateRange />
      </Toolbar>
      <ServerTable<EventRow>
        queryKey={["audit", f]}
        fetchPage={(p) => api<Page<EventRow> & { capped?: boolean }>("/events" + qs({ ...f, ...p, paged: true }))}
        columns={columns} sort={sort} onSort={setSort} rowKey={(r) => r.id} onRowClick={setOpen}
        summary={(total) => `${num(total)}${total > 100000 ? "+" : ""} ${source === "audit" ? "changes" : "events"}`}
        empty={<EmptyState title="Nothing recorded">{source === "audit" ? "Changes made in the dashboard are recorded here." : "Sign-ins and dashboard events are recorded here."} Clear the search or widen the dates.</EmptyState>} />
      <EventDrawer event={open} onClose={() => setOpen(null)} />
    </>
  );
}

/** what a dashboard event was about (a token name, a user name...) */
function p0name(r: EventRow) {
  const p = payload(r);
  return p.name || (r.kind.startsWith("login") ? "" : p.username) || "";
}
