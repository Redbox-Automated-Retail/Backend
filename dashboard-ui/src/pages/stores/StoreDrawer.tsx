import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Alert, Anchor, Button, Divider, Drawer, Group, NumberInput, Select, SimpleGrid, Skeleton, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconTrash } from "@tabler/icons-react";
import { z } from "zod";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { ago } from "@/lib/format";
import { confirm, notifyOk } from "@/lib/notify";
import { AddressSearch, DateField, TimeField } from "@/components/fields";
import { ErrorState } from "@/components/DataTable";
import { useLookups, type StoreRow } from "./api";

const optNum = z.union([z.number(), z.literal("")]).optional();
const schema = z.object({
  store_id: z.union([z.number().int().positive("A positive number"), z.literal("")]).superRefine((v, ctx) => {
    if (v === "") ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Required" });
  }),
  address1: z.string().trim(),
  address2: z.string().optional(),
  city: z.string().trim(),
  county: z.string().optional(),
  state: z.string().nullable(),
  zip: z.string().trim().refine((v) => v === "" || /^\d{5}(-\d{4})?$/.test(v), "ZIP: 12345 or 12345-6789"),
  banner_id: z.string().nullable(),
  market_id: z.string().nullable(),
  return_time: z.string().regex(/^\d{2}:\d{2}$/, "Pick a time"),
  rental_tax_rate: optNum, purchase_tax_rate: optNum, purchase_price: optNum, service_fee_tax_rate: optNum,
  open_date: z.string().nullable(),
  return_only_mode_date: z.string().nullable(),
  sell_thru: z.boolean(),
  offered: z.boolean(),
  due_time: z.string().optional(),
  kaseya_market_name: z.string().optional(),
  isNew: z.boolean(),
}).superRefine((v, ctx) => {
  // a new store needs a full address; imported stores with gaps can still be edited
  if (!v.isNew) return;
  if (!v.address1) ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["address1"], message: "Enter the street address" });
  if (!v.city) ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["city"], message: "Enter the city" });
  if (!v.state) ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["state"], message: "Pick the state" });
  if (!v.zip) ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["zip"], message: "Enter the ZIP" });
});
type Values = z.infer<typeof schema>;

const empty: Values = {
  store_id: "", address1: "", address2: "", city: "", county: "", state: null, zip: "", banner_id: null, market_id: null,
  return_time: "21:00", rental_tax_rate: "", purchase_tax_rate: "", purchase_price: 5, service_fee_tax_rate: "", open_date: null,
  return_only_mode_date: null, sell_thru: true, offered: false, due_time: "", kaseya_market_name: "", isNew: true,
};

function fromRow(r: StoreRow): Values {
  const v = r.store;
  const n = (x: number | null | undefined) => (x == null ? "" : x);
  return {
    store_id: r.store_id, address1: v.address1 || "", address2: v.address2 || "", city: v.city || "", county: v.county || "",
    state: v.state || null, zip: v.zip || "", banner_id: v.banner_id != null ? String(v.banner_id) : null, market_id: v.market_id != null ? String(v.market_id) : null,
    return_time: r.return_time_24 || "21:00", rental_tax_rate: n(v.rental_tax_rate), purchase_tax_rate: n(v.purchase_tax_rate),
    purchase_price: n(v.purchase_price), service_fee_tax_rate: n(v.service_fee_tax_rate), open_date: r.open_date_iso,
    return_only_mode_date: r.return_only_mode_date_iso, sell_thru: !!v.sell_thru, offered: !!r.offered, due_time: r.due_time_24 || "",
    kaseya_market_name: r.kaseya_market_name || "", isNew: false,
  };
}

/** Create (storeId "new") or edit a store. */
export function StoreDrawer({ storeId, onClose }: { storeId: number | "new" | null; onClose: () => void }) {
  const isNew = storeId === "new";
  const can = useCan();
  const qc = useQueryClient();
  const lookups = useLookups();
  const store = useQuery({ queryKey: ["stores", "one", storeId], queryFn: () => api<StoreRow>(`/stores/${storeId}`), enabled: typeof storeId === "number" });
  const form = useForm<Values>({ initialValues: empty, validate: zodResolver(schema) });
  useEffect(() => {
    if (isNew) { form.setValues(empty); form.resetDirty(empty); }
    else if (store.data) { const v = fromRow(store.data); form.setValues(v); form.resetDirty(v); }
  }, [storeId, store.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (v: Values) => {
      const body = { ...v, banner_id: v.banner_id ? Number(v.banner_id) : null, market_id: v.market_id ? Number(v.market_id) : null };
      return isNew ? api<StoreRow>("/stores", { body }) : api<StoreRow>(`/stores/${storeId}`, { method: "PUT", body });
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["stores"] });
      notifyOk(`Store ${r.store_id} saved. It goes to kiosks with the next catalog build.`);
      onClose();
    },
  });
  const del = useMutation({
    mutationFn: () => api(`/stores/${storeId}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["stores"] }); notifyOk(`Store ${storeId} deleted`); onClose(); },
  });
  const askDelete = async () => {
    const r = store.data!;
    if (await confirm({ title: `Delete store ${r.store_id}?`, danger: true, confirmLabel: "Delete store",
      body: <Text size="sm">It is removed from the Store table{r.offered ? " and from the kiosk installer's list" : ""}. {r.last_seen_at ? "Its kiosk keeps running but no longer finds its store." : ""} Kiosks lose it with the next catalog build.</Text> }))
      del.mutate();
  };
  const L = lookups.data;
  const ro = !can();
  return (
    <Drawer opened={storeId !== null} onClose={onClose} position="right" size="xl" title={<Title order={3}>{isNew ? "Add a store" : `Store ${storeId}`}</Title>}>
      {!isNew && store.isError ? <ErrorState error={store.error} onRetry={() => store.refetch()} /> : !isNew && store.isPending ? (
        <Stack>{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} h={36} />)}</Stack>
      ) : (
        <form onSubmit={form.onSubmit((v) => save.mutate(v))}>
          <Stack gap="md">
            <Text size="sm">A physical location. Saved into profile.data's Store table; the kiosk set up as this store reads its address, tax rates, return time, market and banner from it after the next catalog build.</Text>
            {!isNew && store.data?.last_seen_at && <Alert color="blue" variant="light">A kiosk is set up as this store: <Anchor component={Link} to={`/kiosks/${storeId}`} fw={700}>kiosk {storeId}</Anchor>, seen {ago(store.data.last_seen_at)}.</Alert>}
            <NumberInput label="Store ID" required hideControls disabled={!isNew || ro} allowDecimal={false} allowNegative={false} {...form.getInputProps("store_id")} />
            <Divider label="Location" labelPosition="left" />
            {!ro && <AddressSearch description="Search for the address to fill in the fields below." onPick={(a) => {
              form.setValues({ address1: a.address1 || form.values.address1, city: a.city || "", county: a.county || "", state: a.state || "", zip: a.zip || "" });
            }} />}
            <SimpleGrid cols={2}>
              <TextInput label="Street address" withAsterisk={isNew} disabled={ro} {...form.getInputProps("address1")} />
              <TextInput label="Address line 2" disabled={ro} {...form.getInputProps("address2")} />
              <TextInput label="City" withAsterisk={isNew} disabled={ro} {...form.getInputProps("city")} />
              <TextInput label="County" disabled={ro} {...form.getInputProps("county")} />
              <Select label="State" withAsterisk={isNew} clearable searchable disabled={ro} data={L?.states.map((s) => ({ value: s.code, label: `${s.name} (${s.code})` }))} {...form.getInputProps("state")} />
              <TextInput label="ZIP" withAsterisk={isNew} disabled={ro} {...form.getInputProps("zip")} />
            </SimpleGrid>
            <Divider label="Retailer and market" labelPosition="left" />
            <SimpleGrid cols={2}>
              <Select label="Banner" searchable clearable limit={100} disabled={ro} placeholder="none" nothingFoundMessage="No banner; add it on the Banners tab"
                data={L?.banners.map((b) => ({ value: String(b.id), label: b.name }))} {...form.getInputProps("banner_id")} />
              <Select label="Market" searchable clearable limit={100} disabled={ro} placeholder="none" nothingFoundMessage="No market; add it on the Markets tab"
                data={L?.markets.map((m) => ({ value: String(m.id), label: m.name }))} {...form.getInputProps("market_id")} />
            </SimpleGrid>
            <Divider label="Rentals" labelPosition="left" />
            <SimpleGrid cols={2}>
              <TimeField label="Return time" description="Discs are due back by this time" {...form.getInputProps("return_time")} />
              <NumberInput label="Purchase price" prefix="$" decimalScale={2} min={0} disabled={ro} {...form.getInputProps("purchase_price")} />
              <NumberInput label="Rental tax rate" suffix=" %" decimalScale={3} min={0} max={100} disabled={ro} {...form.getInputProps("rental_tax_rate")} />
              <NumberInput label="Purchase tax rate" suffix=" %" decimalScale={3} min={0} max={100} disabled={ro} {...form.getInputProps("purchase_tax_rate")} />
              <NumberInput label="Service fee tax rate" suffix=" %" decimalScale={3} min={0} max={100} disabled={ro} {...form.getInputProps("service_fee_tax_rate")} />
              <Switch label="Sells discs (sell-thru)" mt={30} disabled={ro} {...form.getInputProps("sell_thru", { type: "checkbox" })} />
              <DateField label="Opened" disabled={ro} {...form.getInputProps("open_date")} />
              <DateField label="Return-only from" description="From this date the kiosk only takes returns" disabled={ro} {...form.getInputProps("return_only_mode_date")} />
            </SimpleGrid>
            <Divider label="Kiosk installer" labelPosition="left" />
            <Switch label="Offer this store in the kiosk installer" description="New kiosks can then be set up as this store" disabled={ro} {...form.getInputProps("offered", { type: "checkbox" })} />
            {form.values.offered && (
              <SimpleGrid cols={2}>
                <TimeField label="Due time" description="Shown by the installer" {...form.getInputProps("due_time")} />
                <TextInput label="Kaseya market" disabled={ro} {...form.getInputProps("kaseya_market_name")} />
              </SimpleGrid>
            )}
            {!ro && (
              <Group justify="space-between" mt="md" pos="sticky" bottom={0} py="sm" bg="var(--mantine-color-body)">
                {!isNew ? <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={askDelete} loading={del.isPending}>Delete store</Button> : <span />}
                <Group>
                  <Button variant="default" onClick={onClose}>Cancel</Button>
                  <Button type="submit" loading={save.isPending} disabled={!isNew && !form.isDirty()}>{isNew ? "Add store" : "Save changes"}</Button>
                </Group>
              </Group>
            )}
          </Stack>
        </form>
      )}
    </Drawer>
  );
}
