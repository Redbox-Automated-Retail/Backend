import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Checkbox, Group, Select, SegmentedControl, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { IconTerminal2 } from "@tabler/icons-react";
import { api, qs, type Page } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { ago, num } from "@/lib/format";
import { ServerTable, EmptyState, useUrlSort, type Column } from "@/components/DataTable";
import { PageHeader, StatCards, Status, Toolbar, UrlSearch, useUrlParam } from "@/components/ui";
import { CommandDialog } from "@/components/CommandDialog";
import { ReprovisionButton } from "@/pages/provisioning/Reprovision";

export type KioskRow = {
  kiosk_id: number; market: string | null; banner: string | null; ucs_version: string | null; last_seen_at: string | null;
  versions: Record<string, string> | null; status: "online" | "stale" | "offline"; mqtt_connected: boolean; last_check_in: string | null;
  config_version: number | null; label: string | null; city: string | null; location_state: string | null; discs: number;
};
type KioskPage = Page<KioskRow> & { counts: { online: number; stale: number; offline: number; total: number }; markets: string[] };
export const engineVersion = (v: Record<string, string> | null | undefined) => (v && (v.KioskEngine || v["Kiosk Engine"] || Object.values(v)[0])) || null;
export const kioskStatus = (s: string) => (s === "online" ? <Status kind="ok">Online</Status> : s === "stale" ? <Status kind="warn">Stale</Status> : <Status kind="bad">Offline</Status>);

/** The machines: every kiosk that has called in, problems first. */
export function KiosksPage() {
  const nav = useNavigate();
  const can = useCan();
  const [q] = useUrlParam("q");
  const [status, setStatus] = useUrlParam("status");
  const [market, setMarket] = useUrlParam("market");
  const [sort, setSort] = useUrlSort({ dir: "asc" });
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [cmd, setCmd] = useState(false);
  const filters = { q, status, market };
  const counts = useQuery({ queryKey: ["kiosks", "counts"], queryFn: () => api<KioskPage>("/kiosks?limit=1") });
  const c = counts.data?.counts;
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const columns: Column<KioskRow>[] = [
    ...(can() ? [{ id: "pick", header: "", width: 44, cell: (r: KioskRow) => <Checkbox checked={picked.has(r.kiosk_id)} onChange={() => toggle(r.kiosk_id)} aria-label={`Select kiosk ${r.kiosk_id}`} /> }] : []),
    { id: "id", header: "Kiosk", sort: "kiosk_id", width: 110, cell: (r) => <Text fw={700} size="sm">{r.kiosk_id}</Text> },
    { id: "status", header: "Status", sort: "status", width: 120, cell: (r) => kioskStatus(r.status) },
    { id: "where", header: "Location", sort: "city", cell: (r) => [r.label, r.city, r.location_state].filter(Boolean).join(", ") || <Text span size="sm" c="dark.1">unknown</Text> },
    { id: "market", header: "Market", sort: "market", width: 170, cell: (r) => r.market || "-" },
    { id: "seen", header: "Last seen", sort: "last_seen", width: 140, cell: (r) => ago(r.last_seen_at) },
    { id: "engine", header: "Engine", width: 120, cell: (r) => engineVersion(r.versions) || "-" },
    { id: "ucs", header: "Update Client", sort: "ucs_version", width: 130, cell: (r) => r.ucs_version || "-" },
    { id: "config", header: "Config", sort: "config", width: 90, cell: (r) => (r.config_version ? `v${r.config_version}` : "-") },
    { id: "discs", header: "Discs", sort: "discs", width: 90, align: "right", cell: (r) => num(r.discs) },
  ];
  return (
    <>
      <PageHeader title="Kiosks" description="The machines that have called in. A kiosk takes its location from the store it was set up as."
        actions={can() && <><ReprovisionButton />
          <Button leftSection={<IconTerminal2 size={16} />} disabled={!picked.size} onClick={() => setCmd(true)}>
            {picked.size ? `Command to ${picked.size} selected` : "Select kiosks for a command"}</Button></>} />
      <StatCards loading={counts.isPending} stats={[
        { label: "Kiosks", value: num(c?.total), to: "/kiosks" },
        { label: "Online", value: num(c?.online), color: "teal.4", hint: "connected or seen in the last 20 min", to: "/kiosks?status=online" },
        { label: "Stale", value: num(c?.stale), color: c?.stale ? "yellow.4" : undefined, hint: "not seen for 20 min to a day", to: "/kiosks?status=stale" },
        { label: "Offline", value: num(c?.offline), color: c?.offline ? "red.4" : undefined, hint: "silent for more than a day", to: "/kiosks?status=offline" },
      ]} />
      <Toolbar>
        <UrlSearch placeholder="Kiosk ID, market, city or version" />
        <SegmentedControl value={status || ""} onChange={(v) => setStatus(v || null)} aria-label="Status"
          data={[{ value: "", label: "All" }, { value: "online", label: "Online" }, { value: "stale", label: "Stale" }, { value: "offline", label: "Offline" }]} />
        <Select placeholder="Any market" data={counts.data?.markets || []} value={market} onChange={setMarket} searchable clearable w={200} aria-label="Market" />
        {picked.size > 0 && <Group gap="xs"><Text size="sm" fw={600}>{picked.size} selected</Text><Button size="xs" variant="subtle" onClick={() => setPicked(new Set())}>Clear</Button></Group>}
      </Toolbar>
      <ServerTable<KioskRow> queryKey={["kiosks", "list", filters]} fetchPage={(p) => api<KioskPage>("/kiosks" + qs({ ...filters, ...p }))}
        columns={columns} sort={sort} onSort={setSort} rowKey={(r) => r.kiosk_id} onRowClick={(r) => nav(`/kiosks/${r.kiosk_id}`)}
        summary={(t) => `${num(t)} kiosks${q || status || market ? " match" : ""}`}
        empty={<EmptyState title={q || status || market ? "No kiosk matches" : "No kiosk has called in yet"}>A kiosk appears here the first time it talks to any backend service.</EmptyState>} />
      <CommandDialog kiosks={[...picked]} opened={cmd} onClose={() => setCmd(false)} />
    </>
  );
}
