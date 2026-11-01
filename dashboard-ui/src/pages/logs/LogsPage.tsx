import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Code, Group, Stack, Switch, Text } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { LocalTable, ServerTable, useUrlSort, EmptyState, type Column } from "@/components/DataTable";
import { PageHeader, Section, Toolbar, UrlSearch } from "@/components/ui";
import { api, qs, type Page } from "@/lib/api";
import { ago, dateTime, num } from "@/lib/format";
import { KioskLink, UrlDateRange, UrlSelect, UrlTextInput, jsonPreview, rangeParams } from "./filters";
import { EventDrawer, type EventRow } from "./EventDrawer";

type EventPage = Page<EventRow> & { capped?: boolean };
type Kind = { source: string; kind: string; n: number; last: string };

const KEY = "logs-events";

export function LogsPage() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const [sort, setSort] = useUrlSort({ sort: "id", dir: "desc" });
  const [open, setOpen] = useState<EventRow | null>(null);
  const [live, setLive] = useState(false);
  const kinds = useQuery({ queryKey: ["events-kinds"], queryFn: () => api<Kind[]>("/events/kinds") });

  const f = { kiosk_id: sp.get("kiosk") || undefined, source: sp.get("source") || undefined, kind: sp.get("kind") || undefined,
    q: sp.get("q") || undefined, ...rangeParams(sp.get("since"), sp.get("until")) };

  // live mode: the table's query refetches every 5 s without running the top progress bar
  qc.setQueryDefaults([KEY], live ? { refetchInterval: 5000, meta: { silent: true } } : {});
  useEffect(() => () => qc.setQueryDefaults([KEY], {}), [qc]);

  const sources = useMemo(() => [...new Set((kinds.data || []).map((k) => k.source))].sort(), [kinds.data]);
  const kindOptions = useMemo(() => [...new Set((kinds.data || []).filter((k) => !f.source || k.source === f.source).map((k) => k.kind))].sort(),
    [kinds.data, f.source]);

  const columns: Column<EventRow>[] = [
    { id: "id", header: "Id", cell: (r) => r.id, sort: "id", width: 90 },
    { id: "created_at", header: "Time", cell: (r) => <Text size="sm" style={{ whiteSpace: "nowrap" }}>{dateTime(r.created_at)}</Text>, sort: "created_at", width: 190 },
    { id: "kiosk_id", header: "Kiosk", cell: (r) => <KioskLink id={r.kiosk_id} />, sort: "kiosk_id", width: 90 },
    { id: "source", header: "Source", cell: (r) => r.source, sort: "source", width: 120 },
    { id: "kind", header: "Kind", cell: (r) => <Code>{r.kind}</Code>, sort: "kind", width: 220 },
    { id: "payload", header: "Payload", cell: (r) => <Text size="xs" ff="monospace" truncate="end" maw={520}>{jsonPreview(r.payload, 200)}</Text> },
  ];

  const kindColumns = [
    { id: "source", header: "Source", cell: (k: Kind) => k.source, value: (k: Kind) => k.source },
    { id: "kind", header: "Kind", cell: (k: Kind) => <Code>{k.kind}</Code>, value: (k: Kind) => k.kind },
    { id: "n", header: "Count", cell: (k: Kind) => num(k.n), value: (k: Kind) => Number(k.n), align: "right" as const },
    { id: "last", header: "Last", cell: (k: Kind) => `${dateTime(k.last)} (${ago(k.last)})`, value: (k: Kind) => k.last },
  ];

  const pickKind = (k: Kind) => {
    setSp((p) => { const q = new URLSearchParams(p); q.set("source", k.source); q.set("kind", k.kind); return q; }, { replace: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <>
      <PageHeader stats="logs" title="Logs & events" description="Everything the services record: kiosk calls, IoT traffic, broker calls, dashboard sign-ins and changes"
        actions={<>
          <Switch label="Live (every 5 s)" checked={live} onChange={(e) => setLive(e.currentTarget.checked)} fw={600} />
          <Button variant="default" leftSection={<IconRefresh size={16} />} onClick={() => { qc.invalidateQueries({ queryKey: [KEY] }); kinds.refetch(); }}>Refresh</Button>
        </>} />
      <Toolbar>
        <UrlSearch placeholder="Search kind or payload" />
        <UrlTextInput param="kiosk" label="Kiosk" placeholder="Any kiosk" digits w={120} />
        <UrlSelect param="source" label="Source" placeholder="Any source" data={sources} w={160} />
        <UrlSelect param="kind" label="Kind" placeholder="Any kind" data={kindOptions} w={220} />
        <UrlDateRange />
      </Toolbar>
      <ServerTable<EventRow>
        queryKey={[KEY, f]}
        fetchPage={(p) => api<EventPage>("/events" + qs({ ...f, ...p, paged: true }))}
        columns={columns} sort={sort} onSort={setSort} rowKey={(r) => r.id} onRowClick={setOpen}
        summary={(total, loaded) => <Group gap="xs">{`${num(total)} events${loaded < total ? `, ${num(loaded)} loaded` : ""}`}{live && <Badge color="teal" variant="light">live</Badge>}</Group>}
        empty={<EmptyState title="No events match">Clear the filters or widen the date range; services record events as kiosks call in.</EmptyState>} />
      <Stack mt="xl">
        <Section title="Event kinds" actions={<Text size="sm" c="dark.1">Click a row to filter by it</Text>} p={0}>
          <LocalTable rows={kinds.data} loading={kinds.isPending} error={kinds.error} onRetry={() => kinds.refetch()} columns={kindColumns}
            rowKey={(k) => k.source + "/" + k.kind} onRowClick={pickKind} maxHeight={480} initialSort={{ sort: "n", dir: "desc" }}
            empty={<EmptyState title="No events yet">Events appear once kiosks or the dashboard do something.</EmptyState>} />
        </Section>
      </Stack>
      <EventDrawer event={open} onClose={() => setOpen(null)} />
    </>
  );
}
