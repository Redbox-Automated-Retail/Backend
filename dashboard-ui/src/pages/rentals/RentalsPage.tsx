import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Drawer, Stack, Text } from "@mantine/core";
import { ServerTable, useUrlSort, EmptyState, type Column } from "@/components/DataTable";
import { Facts, PageHeader, RouteTabs, Status, Toolbar, UrlSearch } from "@/components/ui";
import { api, qs, type Page } from "@/lib/api";
import { ago, dateTime, num } from "@/lib/format";
import { KioskLink, UrlSelect, UrlTextInput } from "../logs/filters";

type Rental = {
  id: number; kiosk_id: number; barcode: string | null; product_id: number | null; product_type?: string | null; status?: string;
  rented_at?: string; returned_at?: string | null; return_kiosk_id?: number | null; title: string | null; type: string | null;
  deck?: number | null; slot?: number | null; return_date?: string | null; created_at?: string;
};

const STATUSES = [
  { value: "out", label: "Out" }, { value: "returned", label: "Returned" },
  { value: "purchased", label: "Purchased" }, { value: "not_vended", label: "Not vended" },
];

function StatusCell({ s }: { s?: string }) {
  if (s === "out") return <Status kind="warn">Out</Status>;
  if (s === "returned") return <Status kind="ok">Returned</Status>;
  if (s === "not_vended") return <Status kind="bad">Not vended</Status>;
  return <Status kind="off">{s || "-"}</Status>;
}

function TitleCell({ r }: { r: Rental }) {
  return r.title
    ? <Text size="sm" fw={600} truncate="end">{r.title} {r.type && <Text span size="xs" c="dark.1" fw={500}>{r.type}</Text>}</Text>
    : <Text size="sm" c="dark.1">product {r.product_id ?? "?"}</Text>;
}

export function RentalsPage() {
  const { tab = "rentals" } = useParams();
  const kind = tab === "returns" ? "returns" : "rentals";
  const [sp] = useSearchParams();
  const [sort, setSort] = useUrlSort({ sort: "when", dir: "desc" });
  const [open, setOpen] = useState<Rental | null>(null);
  const f = { kind, q: sp.get("q") || undefined, kiosk_id: sp.get("kiosk") || undefined, status: kind === "rentals" ? sp.get("status") || undefined : undefined };

  const when = (r: Rental) => (kind === "rentals" ? r.rented_at : r.return_date || r.created_at);
  const columns: Column<Rental>[] = [
    { id: "when", header: kind === "rentals" ? "Rented" : "Returned", sort: "when", width: 190, cell: (r) => <Text size="sm" style={{ whiteSpace: "nowrap" }}>{dateTime(when(r))}</Text> },
    { id: "kiosk_id", header: "Kiosk", sort: "kiosk_id", width: 90, cell: (r) => <KioskLink id={r.kiosk_id} /> },
    { id: "title", header: "Title", sort: "title", cell: (r) => <TitleCell r={r} /> },
    { id: "barcode", header: "Barcode", sort: "barcode", width: 150, cell: (r) => r.barcode || "-" },
    ...(kind === "rentals" ? [
      { id: "status", header: "Status", sort: "status", width: 130, cell: (r: Rental) => <StatusCell s={r.status} /> },
      { id: "returned_at", header: "Back", sort: "returned_at", width: 190, cell: (r: Rental) => r.returned_at
        ? <Text size="sm">{ago(r.returned_at)}{r.return_kiosk_id && r.return_kiosk_id !== r.kiosk_id ? <> to <KioskLink id={r.return_kiosk_id} /></> : null}</Text> : "" },
    ] : [
      { id: "slot", header: "Deck / slot", width: 140, cell: (r: Rental) => (r.deck != null ? `deck ${r.deck}, slot ${r.slot}` : "") },
    ]),
  ];

  return (
    <>
      <PageHeader stats="rentals" title="Rentals & returns" description="Discs rented, bought and returned at the kiosks" />
      <RouteTabs base="/rentals" value={kind} tabs={[{ value: "rentals", label: "Rentals" }, { value: "returns", label: "Returns" }]} />
      <Toolbar>
        <UrlSearch placeholder="Barcode or title" />
        <UrlTextInput param="kiosk" label="Kiosk" placeholder="Any kiosk" digits w={120} />
        {kind === "rentals" && <UrlSelect param="status" label="Status" placeholder="Any status" data={STATUSES} w={170} />}
      </Toolbar>
      <ServerTable<Rental>
        queryKey={["rentals", f]}
        fetchPage={(p) => api<Page<Rental>>("/rentals" + qs({ ...f, ...p }))}
        columns={columns} sort={sort} onSort={setSort} rowKey={(r) => r.id} onRowClick={setOpen}
        summary={(total) => `${num(total)} ${kind}`}
        empty={<EmptyState title={kind === "rentals" ? "No rentals" : "No returns"}>
          {f.q || f.kiosk_id || f.status ? "Nothing matches these filters; clear them to see everything." : `They appear here when kiosks report ${kind} through the Transaction API.`}
        </EmptyState>} />
      <Drawer opened={!!open} onClose={() => setOpen(null)} position="right" size="lg" title={<Text fw={800}>{open?.title || `Product ${open?.product_id ?? "?"}`}</Text>}>
        {open && (
          <Stack>
            <Facts cols={2} items={kind === "rentals" ? [
              ["Kiosk", <KioskLink id={open.kiosk_id} />], ["Barcode", open.barcode], ["Product", open.product_id], ["Type", open.type || open.product_type],
              ["Status", <StatusCell s={open.status} />], ["Rented", dateTime(open.rented_at)], ["Returned", open.returned_at ? dateTime(open.returned_at) : "-"],
              ["Returned to", open.return_kiosk_id ? <KioskLink id={open.return_kiosk_id} /> : "-"],
            ] : [
              ["Kiosk", <KioskLink id={open.kiosk_id} />], ["Barcode", open.barcode], ["Product", open.product_id], ["Type", open.type],
              ["Returned", dateTime(open.return_date || open.created_at)], ["Recorded", dateTime(open.created_at)],
              ["Deck", open.deck ?? "-"], ["Slot", open.slot ?? "-"],
            ]} />
          </Stack>
        )}
      </Drawer>
    </>
  );
}
