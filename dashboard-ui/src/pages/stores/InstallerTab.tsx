import { Link } from "react-router-dom";
import { Anchor, Button, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { ago } from "@/lib/format";
import { notifyOk } from "@/lib/notify";
import { EmptyState, LocalTable } from "@/components/DataTable";
import { Status } from "@/components/ui";

type Entry = { id: number; state_name: string; banner_name: string; address: string | null; city: string | null; zip: string | null;
  market_name: string | null; due_time: string | null; pending: boolean; last_seen_at: string | null };

/** What the kiosk installer offers during setup (Proxy API /api/installer/getpending*: state, banner, store). */
export function InstallerTab() {
  const can = useCan();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["stores", "installer"], queryFn: () => api<Entry[]>("/stores/installer") });
  const offer = useMutation({
    mutationFn: (e: Entry) => api(`/stores/${e.id}/offer`, { body: { offered: !e.pending } }),
    onSuccess: (_r, e) => { qc.invalidateQueries({ queryKey: ["stores"] }); notifyOk(e.pending ? `Store ${e.id} is no longer offered` : `Store ${e.id} is offered again`); },
  });
  return (
    <>
      <Text size="sm" mb="md">During setup the kiosk installer lists the offered stores by state and banner; picking one gives the new kiosk its store ID, address and market.
        Offer a store from its page on the Stores tab, and stop offering it once its kiosk is installed.</Text>
      <LocalTable<Entry> rows={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => r.id}
        initialSort={{ sort: "pending", dir: "desc" }}
        columns={[
          { id: "id", header: "Store", value: (r) => r.id, cell: (r) => <Anchor component={Link} to={`/stores/stores?q=${r.id}`} fw={700} size="sm">{r.id}</Anchor> },
          { id: "state", header: "State", value: (r) => r.state_name, cell: (r) => r.state_name },
          { id: "banner", header: "Banner", value: (r) => r.banner_name, cell: (r) => r.banner_name },
          { id: "address", header: "Address", value: (r) => r.address, cell: (r) => [r.address, r.city, r.zip].filter(Boolean).join(", ") },
          { id: "market", header: "Market", value: (r) => r.market_name, cell: (r) => r.market_name || "-" },
          { id: "due", header: "Due time", value: (r) => r.due_time, cell: (r) => r.due_time || "-" },
          { id: "pending", header: "Installer", value: (r) => (r.pending ? 1 : 0), cell: (r) => (r.pending ? <Status kind="ok">Offered</Status> : <Status kind="off">Not offered</Status>) },
          { id: "kiosk", header: "Kiosk", value: (r) => r.last_seen_at, cell: (r) => r.last_seen_at ? <Anchor component={Link} to={`/kiosks/${r.id}`} size="sm" fw={600}>seen {ago(r.last_seen_at)}</Anchor> : "not set up yet" },
          { id: "act", header: "", cell: (r) => can() && <Button size="xs" variant={r.pending ? "default" : "light"} loading={offer.isPending && offer.variables?.id === r.id}
              onClick={() => offer.mutate(r)}>{r.pending ? "Stop offering" : "Offer"}</Button> },
        ]}
        empty={<EmptyState title="No store is offered to the kiosk installer">Open a store on the Stores tab and switch on "Offer this store in the kiosk installer".</EmptyState>} />
    </>
  );
}
