// Barcodes (cloud_barcodes -> inventory.data): the disc barcode -> title map the kiosks use.
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Drawer, Group, NumberInput, Select, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { api, qs, type Page } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { dateTime, num } from "@/lib/format";
import { EmptyState, ServerTable } from "@/components/DataTable";
import { Toolbar, UrlSearch, useUrlParam } from "@/components/ui";
import { BARCODE_STATUS, type Barcode } from "./shared";

const STATUS_DATA = Object.entries(BARCODE_STATUS).map(([v, l]) => ({ value: v, label: `${v} ${l}` }));

export function BarcodesTab() {
  const can = useCan()("change");
  const qc = useQueryClient();
  const [q] = useUrlParam("q");
  const [pid, setPid] = useUrlParam("product");
  const [open, setOpen] = useUrlParam("barcode");
  const [picked, setPicked] = useState<Barcode | null>(null);
  const current = open && open !== "new" ? (picked?.barcode === open ? picked : { barcode: open, status: 0 } as Barcode) : null;
  const del = useMutation({
    mutationFn: (b: string) => api("/catalog/barcodes/delete", { body: { barcode: b } }),
    onSuccess: (_r, b) => { notifyOk(`Barcode ${b} deleted`); qc.invalidateQueries({ queryKey: ["catalog"] }); },
  });
  const ask = async (b: Barcode) => {
    if (await confirm({ title: `Delete barcode ${b.barcode}?`, danger: true, confirmLabel: "Delete barcode",
      body: `Kiosks stop recognising this disc as title ${b.product_id} after the next publish; its rental count goes with it.` })) del.mutate(b.barcode);
  };
  const what = pid ? `Barcodes of title ${pid}` : q ? `Barcode ${q} and the ones after it` : "Barcodes added here or learned from kiosk transactions, newest first. Imported barcodes show per title (filter by product id) or by search.";
  return (
    <>
      <Toolbar>
        <UrlSearch placeholder="Barcode (shows it and the ones after it)" />
        <NumberInput w={170} placeholder="Product id" aria-label="Product id" allowDecimal={false} min={1} hideControls
          value={pid || ""} onChange={(v) => setPid(v === "" ? null : String(v))} />
        {can && <Button leftSection={<IconPlus size={16} />} onClick={() => setOpen("new")}>Add barcode</Button>}
      </Toolbar>
      <Text size="sm" fw={600} mb="xs">{what}</Text>
      <ServerTable<Barcode> queryKey={["catalog", "barcodes", q || "", pid || ""]} rowKey={(b) => b.barcode}
        fetchPage={(p) => api<Page<Barcode>>("/catalog/barcodes" + qs({ q, product_id: pid, paged: true, limit: p.limit, offset: p.offset }))}
        summary={(t, n) => (q && !pid ? `${num(n)} loaded; scroll for more` : `${num(t)} barcodes`)}
        onRowClick={can ? (b) => { setPicked(b); setOpen(b.barcode); } : undefined} height="calc(100vh - 360px)"
        empty={<EmptyState title="No barcodes here">{pid || q ? "Nothing matches; check the number." : "None added or learned yet. Add one with Add barcode, or search for an imported one."}</EmptyState>}
        columns={[
          { id: "barcode", header: "Barcode", cell: (b) => b.barcode },
          { id: "pid", header: "Product id", cell: (b) => b.product_id },
          { id: "status", header: "Status", cell: (b) => BARCODE_STATUS[b.status] ?? b.status },
          { id: "rentals", header: "Rentals", cell: (b) => num(b.rental_count), align: "right" },
          { id: "source", header: "Source", cell: (b) => b.source },
          { id: "updated", header: "Updated", cell: (b) => dateTime(b.updated_at) },
          ...(can ? [{ id: "x", header: "", cell: (b: Barcode) => <Button size="compact-xs" color="red" variant="subtle" onClick={() => ask(b)}>Delete</Button> }] : []),
        ]} />
      <BarcodeDrawer barcode={current} opened={!!open} onClose={() => setOpen(null)} onDelete={(b) => { setOpen(null); ask(b); }} />
    </>
  );
}

const schema = z.object({
  barcode: z.string().trim().regex(/^\d+$/, "Digits only"),
  product_id: z.union([z.number(), z.string()]).refine((v) => v !== "" && Number(v) > 0, "Which title is this disc?"),
  status: z.string(),
});
type Values = { barcode: string; product_id: number | string; status: string };

function BarcodeDrawer({ barcode, opened, onClose, onDelete }: { barcode: Barcode | null; opened: boolean; onClose: () => void; onDelete: (b: Barcode) => void }) {
  const qc = useQueryClient();
  const init = (): Values => ({ barcode: barcode?.barcode || "", product_id: barcode?.product_id ?? "", status: String(barcode?.status ?? 0) });
  const form = useForm<Values>({ initialValues: init(), validate: zodResolver(schema) });
  useEffect(() => { const v = init(); form.setInitialValues(v); form.setValues(v); form.clearErrors(); }, [barcode?.barcode, opened]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: (v: Values) => api("/catalog/barcodes", { body: { barcode: v.barcode.trim(), product_id: Number(v.product_id), status: Number(v.status) } }),
    onSuccess: (_r, v) => { notifyOk(`Barcode ${v.barcode.trim()} saved`); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
  });
  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="lg" title={<Text fw={800} size="lg">{barcode ? `Barcode ${barcode.barcode}` : "Add a barcode"}</Text>}>
      <form onSubmit={form.onSubmit((v) => save.mutate(v))}>
        <Stack gap="sm">
          <TextInput label="Barcode" withAsterisk disabled={!!barcode} {...form.getInputProps("barcode")} />
          <NumberInput label="Product id" withAsterisk allowDecimal={false} min={1} hideControls {...form.getInputProps("product_id")} />
          <Select label="Status" data={STATUS_DATA} allowDeselect={false} {...form.getInputProps("status")} />
          {!barcode && <Alert variant="light" color="gray">An existing barcode is moved to this title (and marked as added here).</Alert>}
          <Group justify="space-between" mt="md">
            {barcode?.product_id != null ? <Button color="red" variant="light" leftSection={<IconTrash size={16} />} onClick={() => onDelete(barcode)}>Delete barcode</Button> : <span />}
            <Button type="submit" loading={save.isPending} disabled={!form.isDirty()}>Save</Button>
          </Group>
        </Stack>
      </form>
    </Drawer>
  );
}
