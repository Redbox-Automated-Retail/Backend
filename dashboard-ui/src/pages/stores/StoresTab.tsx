import { useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Button, Group, Select, SegmentedControl, Text } from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import { api, qs, type Page } from "@/lib/api";
import { ago } from "@/lib/format";
import { ServerTable, EmptyState, useUrlSort, type Column } from "@/components/DataTable";
import { Status, Toolbar, UrlSearch, useUrlParam } from "@/components/ui";
import { useLookups, type StoreRow } from "./api";
import { StoreDrawer } from "./StoreDrawer";

export function StoresTab({ creating, onCreated }: { creating: boolean; onCreated: () => void }) {
  const lookups = useLookups();
  const [q] = useUrlParam("q");
  const [state, setState] = useUrlParam("state");
  const [banner, setBanner] = useUrlParam("banner");
  const [market, setMarket] = useUrlParam("market");
  const [offered, setOffered] = useUrlParam("offered");
  const [kiosk, setKiosk] = useUrlParam("kiosk");
  const [sort, setSort] = useUrlSort({ dir: "asc" });
  const [open, setOpen] = useState<number | null>(null);
  const filters = { q, state, banner_id: banner, market_id: market, offered, has_kiosk: kiosk };
  const anyFilter = Object.values(filters).some(Boolean);
  const L = lookups.data;
  const columns: Column<StoreRow>[] = [
    { id: "id", header: "Store", sort: "store_id", width: 110, cell: (r) => <Group gap={6} wrap="nowrap"><Text fw={700} size="sm">{r.store_id}</Text>{r.edited_at && <Badge size="xs" color="yellow" variant="light">edited</Badge>}</Group> },
    { id: "banner", header: "Banner", sort: "banner", width: 210, cell: (r) => r.banner || <Text span size="sm" c="dark.1">none</Text> },
    { id: "address", header: "Address", sort: "address", cell: (r) => [r.store.address1, r.store.address2].filter(Boolean).join(" ") || r.store.address || "" },
    { id: "city", header: "City", sort: "city", width: 160, cell: (r) => r.store.city || "" },
    { id: "state", header: "State", sort: "state", width: 74, cell: (r) => r.store.state || "" },
    { id: "market", header: "Market", sort: "market", width: 170, cell: (r) => r.market || <Text span size="sm" c="dark.1">none</Text> },
    { id: "offered", header: "Installer", sort: "offered", width: 110, cell: (r) => r.offered ? <Status kind="ok">Offered</Status> : <Text span size="sm" c="dark.1">-</Text> },
    { id: "kiosk", header: "Kiosk", sort: "last_seen", width: 130, cell: (r) => r.last_seen_at
      ? <Anchor component={Link} to={`/kiosks/${r.store_id}`} size="sm" fw={600}>seen {ago(r.last_seen_at)}</Anchor> : <Text span size="sm" c="dark.1">none</Text> },
  ];
  return (
    <>
      <Toolbar>
        <UrlSearch placeholder="Store ID, address, banner or market" />
        <Select placeholder="Any state" data={L?.states.map((s) => ({ value: s.code, label: `${s.name} (${s.code})` }))} value={state} onChange={setState}
          searchable clearable w={190} aria-label="State" />
        <Select placeholder="Any banner" data={L?.banners.map((b) => ({ value: String(b.id), label: b.name }))} value={banner} onChange={setBanner}
          searchable clearable limit={80} w={210} aria-label="Banner" nothingFoundMessage="No banner" />
        <Select placeholder="Any market" data={L?.markets.map((m) => ({ value: String(m.id), label: m.name }))} value={market} onChange={setMarket}
          searchable clearable limit={80} w={190} aria-label="Market" />
        <SegmentedControl value={offered || ""} onChange={(v) => setOffered(v || null)} aria-label="Installer"
          data={[{ value: "", label: "All" }, { value: "1", label: "Offered" }, { value: "0", label: "Not offered" }]} />
        <SegmentedControl value={kiosk || ""} onChange={(v) => setKiosk(v || null)} aria-label="Kiosk"
          data={[{ value: "", label: "Any" }, { value: "1", label: "With kiosk" }, { value: "0", label: "No kiosk" }]} />
        {anyFilter && <Button variant="subtle" color="gray" leftSection={<IconX size={14} />} component={Link} to="/stores/stores">Clear filters</Button>}
      </Toolbar>
      <ServerTable<StoreRow>
        queryKey={["stores", "list", filters]}
        fetchPage={(p) => api<Page<StoreRow>>("/stores" + qs({ ...filters, ...p }))}
        columns={columns} sort={sort} onSort={setSort} rowKey={(r) => r.store_id} onRowClick={(r) => setOpen(r.store_id)}
        summary={(t, n) => `${t.toLocaleString()} stores${anyFilter ? " match" : ""}${n < t ? ` · ${n.toLocaleString()} loaded` : ""}`}
        empty={<EmptyState title={anyFilter ? "No store matches" : "No stores yet"}>{anyFilter ? "Change or clear the filters." : "Add a store, or import the stores from seed data on System administration."}</EmptyState>}
      />
      <StoreDrawer storeId={creating ? "new" : open} onClose={() => { setOpen(null); if (creating) onCreated(); }} />
    </>
  );
}
