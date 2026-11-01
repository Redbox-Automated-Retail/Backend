import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ActionIcon, Alert, Button, Checkbox, Code, Drawer, Group, NumberInput, Select, SimpleGrid, Stack, Text, TextInput, Tooltip,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconAlertTriangle, IconPlus, IconRefresh, IconTrash, IconWifi } from "@tabler/icons-react";
import { LocalTable, EmptyState } from "@/components/DataTable";
import { Facts, PageHeader, Section, StatCards, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { notifyError, notifyOk } from "@/lib/notify";
import { ago, dateTime, num } from "@/lib/format";
import { JsonBlock, KioskLink, jsonPreview } from "../logs/filters";

type BrokerKiosk = {
  kiosk_id: number; version: Record<string, unknown> | null; host: string | null; connected: boolean; registrations: number;
  registered_at: string; disconnected_at: string | null; live: boolean | null;
  connection: "connected" | "lost" | "not answering" | "not connected" | "unknown"; lost_at: string | null; lost_reason: string | null;
};
type Call = { id: number; kiosk_id: number; kind: string; reference_number: number | null; request: unknown; result: unknown; success: boolean | null; error: string | null; created_at: string };
type Broker = { reachable: boolean; kiosks: BrokerKiosk[]; requests: Call[] };

function Connection({ k }: { k: BrokerKiosk }) {
  if (k.connection === "connected") return <Status kind="ok">Connected</Status>;
  if (k.connection === "lost" || k.connection === "not answering")
    return <div><Status kind="warn">{k.connection === "lost" ? "Lost" : "Not answering"}</Status>
      <Text size="xs" c="dark.1">{k.lost_at ? ago(k.lost_at) : ""} {k.lost_reason || ""}</Text></div>;
  if (k.connection === "unknown") return <Status kind="warn">Unknown</Status>;
  return <Status kind="bad">Not connected</Status>;
}

const reserveSchema = z.object({
  kiosk_id: z.string({ invalid_type_error: "Pick a kiosk" }).min(1, "Pick a kiosk"),
  email: z.string().trim().email("Enter the customer's e-mail address"),
  last4: z.string().refine((v) => !v || /^\d{4}$/.test(v), "Four digits, or leave it empty"),
  titles: z.array(z.object({
    title_id: z.number({ invalid_type_error: "Title id" }).int().positive("Title id"),
    price: z.union([z.number().nonnegative("Not negative"), z.literal("")]),
    purchase: z.boolean(),
  })).min(1, "Add at least one title"),
  authorize_at_pickup: z.boolean(),
});
type ReserveValues = { kiosk_id: string | null; email: string; last4: string; titles: { title_id: number | ""; price: number | ""; purchase: boolean }[]; authorize_at_pickup: boolean };

function ReserveForm({ kiosks, disabled }: { kiosks: string[]; disabled: boolean }) {
  const qc = useQueryClient();
  const [out, setOut] = useState<unknown>(null);
  const form = useForm<ReserveValues>({
    initialValues: { kiosk_id: kiosks[0] || null, email: "", last4: "", titles: [{ title_id: "", price: "", purchase: false }], authorize_at_pickup: true },
    validate: zodResolver(reserveSchema),
  });
  const send = useMutation({
    mutationFn: (v: ReserveValues) => api<{ reference_number: number; result: { ReservationSucceeded?: boolean; ErrorMessage?: string } }>("/broker/reserve", { body: {
      kiosk_id: v.kiosk_id, email: v.email.trim(), last4: v.last4, authorize_at_pickup: v.authorize_at_pickup,
      titles: v.titles.map((t) => ({ title_id: t.title_id, price: t.price === "" ? undefined : t.price, type: t.purchase ? "purchase" : "rental" })),
    } }),
    onSuccess: (x) => {
      setOut(x);
      notifyOk(`Reservation ${x.reference_number}: ${x.result?.ReservationSucceeded ? "reserved" : "not reserved"}`);
      qc.invalidateQueries({ queryKey: ["broker"] });
    },
    onError: (e) => { notifyError(e); setOut({ error: e instanceof Error ? e.message : String(e) }); qc.invalidateQueries({ queryKey: ["broker"] }); },
  });
  return (
    <Section title="Send a reservation to a kiosk">
      <form onSubmit={form.onSubmit((v) => send.mutate(v))}>
        <Stack>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Select label="Kiosk" data={kiosks} placeholder={kiosks.length ? "Pick a kiosk" : "No kiosk connected"} allowDeselect={false} {...form.getInputProps("kiosk_id")} />
            <TextInput label="Customer e-mail" type="email" required {...form.getInputProps("email")} />
          </SimpleGrid>
          <TextInput label="Card last four" maxLength={4} w={160} inputMode="numeric" {...form.getInputProps("last4")} />
          <Stack gap={6}>
            <Text size="sm" fw={500}>Titles</Text>
            {form.values.titles.map((_, i) => (
              <Group key={i} gap="xs" align="flex-start" wrap="nowrap">
                <NumberInput placeholder="Title id" aria-label="Title id" allowDecimal={false} allowNegative={false} hideControls w={130} {...form.getInputProps(`titles.${i}.title_id`)} />
                <NumberInput placeholder="Price (1.99)" aria-label="Kiosk price" decimalScale={2} allowNegative={false} hideControls w={130} {...form.getInputProps(`titles.${i}.price`)} />
                <Checkbox label="Purchase" mt={8} {...form.getInputProps(`titles.${i}.purchase`, { type: "checkbox" })} />
                <Tooltip label="Remove"><ActionIcon variant="subtle" color="gray" mt={4} disabled={form.values.titles.length === 1}
                  onClick={() => form.removeListItem("titles", i)} aria-label="Remove title"><IconTrash size={16} /></ActionIcon></Tooltip>
              </Group>
            ))}
            {typeof form.errors.titles === "string" && <Text c="red.4" size="sm">{form.errors.titles}</Text>}
            <Group><Button size="xs" variant="default" leftSection={<IconPlus size={14} />} onClick={() => form.insertListItem("titles", { title_id: "", price: "", purchase: false })}>Add a title</Button></Group>
          </Stack>
          <Checkbox label="Authorize at pickup" {...form.getInputProps("authorize_at_pickup", { type: "checkbox" })} />
          <Group><Button type="submit" loading={send.isPending} disabled={disabled}>Reserve</Button>
            {send.isPending && <Text size="sm">Waiting for the kiosk (up to 90 s)...</Text>}</Group>
          {out != null && <JsonBlock value={out} mah={300} />}
        </Stack>
      </form>
    </Section>
  );
}

const cancelSchema = z.object({
  kiosk_id: z.string({ invalid_type_error: "Pick a kiosk" }).min(1, "Pick a kiosk"),
  reference_number: z.number({ invalid_type_error: "Enter the reference number" }).int().positive("Enter the reference number"),
});

function CancelForm({ kiosks, disabled }: { kiosks: string[]; disabled: boolean }) {
  const qc = useQueryClient();
  const [out, setOut] = useState<unknown>(null);
  const form = useForm<{ kiosk_id: string | null; reference_number: number | "" }>({
    initialValues: { kiosk_id: kiosks[0] || null, reference_number: "" }, validate: zodResolver(cancelSchema),
  });
  const after = () => qc.invalidateQueries({ queryKey: ["broker"] });
  const cancel = useMutation({
    mutationFn: (v: typeof form.values) => api<{ result: { CancellationSucceeded?: boolean } }>("/broker/cancel", { body: v }),
    onSuccess: (x) => { setOut(x); notifyOk(x.result?.CancellationSucceeded ? "Reservation cancelled" : "The kiosk did not cancel it"); after(); },
    onError: (e) => { notifyError(e); setOut({ error: e instanceof Error ? e.message : String(e) }); after(); },
  });
  const ping = useMutation({
    mutationFn: (kiosk_id: string) => api("/broker/ping", { body: { kiosk_id } }),
    onSuccess: (x) => { setOut(x); notifyOk("The kiosk answered the ping"); after(); },
    onError: (e) => { notifyError(e); setOut({ error: e instanceof Error ? e.message : String(e) }); after(); },
  });
  return (
    <Section title="Cancel a reservation, or ping a kiosk">
      <form onSubmit={form.onSubmit((v) => cancel.mutate(v))}>
        <Stack>
          <Select label="Kiosk" data={kiosks} placeholder={kiosks.length ? "Pick a kiosk" : "No kiosk connected"} allowDeselect={false} {...form.getInputProps("kiosk_id")} />
          <NumberInput label="Reference number" allowDecimal={false} allowNegative={false} hideControls {...form.getInputProps("reference_number")} />
          <Text size="sm" c="dark.1">Engines from 2014-2016 cannot cancel through the broker; their hold ends when the reservation expires.</Text>
          <Group>
            <Button type="submit" color="red" variant="light" loading={cancel.isPending} disabled={disabled}>Cancel reservation</Button>
            <Button variant="default" leftSection={<IconWifi size={16} />} loading={ping.isPending} disabled={disabled || !form.values.kiosk_id}
              onClick={() => form.values.kiosk_id && ping.mutate(form.values.kiosk_id)}>Ping kiosk</Button>
          </Group>
          {out != null && <JsonBlock value={out} mah={300} />}
        </Stack>
      </form>
    </Section>
  );
}

export function BrokerPage() {
  const can = useCan()("change");
  const q = useQuery({ queryKey: ["broker"], queryFn: () => api<Broker>("/broker") });
  const [open, setOpen] = useState<Call | null>(null);
  const [kiosk, setKiosk] = useState<BrokerKiosk | null>(null);
  const d = q.data;
  const live = (d?.kiosks || []).filter((k) => k.connection === "connected" || k.connection === "not answering").map((k) => String(k.kiosk_id));
  const failed = (d?.requests || []).filter((r) => !r.success).length;

  return (
    <>
      <PageHeader title="Reservation broker"
        description="Kiosks registered with the legacy .NET Remoting broker; send, cancel and test online reservations"
        actions={<Button variant="default" leftSection={<IconRefresh size={16} />} loading={q.isFetching} onClick={() => q.refetch()}>Refresh</Button>} />
      {d && !d.reachable && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />} mb="md" title="rb-remoting is not reachable">
          The connection state of the kiosks is unknown and nothing can be sent until the remoting host answers. See System health.
        </Alert>
      )}
      <StatCards loading={!d} stats={[
        { label: "Registered kiosks", value: num(d?.kiosks.length) },
        { label: "Connected now", value: num(live.length), color: live.length ? "teal.4" : undefined },
        { label: "Recent calls", value: num(d?.requests.length), hint: "the last 50" },
        { label: "Failed calls", value: num(failed), color: failed ? "red.4" : undefined, hint: "of the last 50" },
      ]} />
      <Stack gap="lg">
        <Text size="sm" fw={500}>
          Kiosks with <Code>UseLegacyBrokerService</Code> register with the broker (<Code>gtcp://...:7890/RegistrationBroker/Default.rem</Code>, hosted on Mono in
          rb-remoting) and stay connected; the broker then calls back into them. A reservation sent here makes the kiosk hold the discs (Reserve3) and is stored like any
          other reservation, so the pickup goes through the Transaction API.
        </Text>
        <Section title="Kiosks" p={0}>
          <LocalTable<BrokerKiosk> rows={d?.kiosks} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => r.kiosk_id} onRowClick={setKiosk} columns={[
            { id: "kiosk_id", header: "Kiosk", value: (r) => r.kiosk_id, cell: (r) => <KioskLink id={r.kiosk_id} />, width: 90 },
            { id: "connection", header: "Connection", value: (r) => r.connection, cell: (r) => <Connection k={r} /> },
            { id: "registered_at", header: "Registered", value: (r) => r.registered_at, cell: (r) => dateTime(r.registered_at) },
            { id: "registrations", header: "Registrations", align: "right", value: (r) => r.registrations, cell: (r) => num(r.registrations) },
            { id: "version", header: "Engine", value: (r) => String(r.version?.Version ?? ""), cell: (r) => (r.version?.Version as string) || "-" },
            { id: "cancel", header: "Can cancel", value: (r) => (r.version?.CancelReservation ? 1 : 0), cell: (r) => (r.version?.CancelReservation ? "yes" : "no") },
            { id: "disconnected_at", header: "Disconnected", value: (r) => r.disconnected_at, cell: (r) => (r.disconnected_at ? dateTime(r.disconnected_at) : "-") },
          ]} empty={<EmptyState title="No kiosk has registered with the broker">Kiosks register when their engine starts with UseLegacyBrokerService on.</EmptyState>} />
        </Section>
        {can && d && (
          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="lg">
            <ReserveForm key={live.join()} kiosks={live} disabled={!live.length} />
            <CancelForm key={"c" + live.join()} kiosks={live} disabled={!live.length} />
          </SimpleGrid>
        )}
        <Section title="Broker calls" p={0}>
          <LocalTable<Call> rows={d?.requests} loading={q.isPending} rowKey={(r) => r.id} onRowClick={setOpen} initialSort={{ sort: "created_at", dir: "desc" }} columns={[
            { id: "created_at", header: "Time", value: (r) => r.created_at, cell: (r) => <Text size="sm" style={{ whiteSpace: "nowrap" }}>{dateTime(r.created_at)}</Text> },
            { id: "kiosk_id", header: "Kiosk", value: (r) => r.kiosk_id, cell: (r) => <KioskLink id={r.kiosk_id} /> },
            { id: "kind", header: "Call", value: (r) => r.kind, cell: (r) => <Text size="sm" fw={600} tt="capitalize">{r.kind}</Text> },
            { id: "reference_number", header: "Reference", value: (r) => r.reference_number, cell: (r) => r.reference_number ?? "-" },
            { id: "success", header: "Result", value: (r) => (r.success ? 1 : 0), cell: (r) => r.success ? <Status kind="ok">OK</Status> : <Status kind="bad">Failed</Status> },
            { id: "detail", header: "Details", cell: (r) => <Text size="xs" ff="monospace" truncate="end" maw={420}>{r.error || jsonPreview(r.result, 160)}</Text> },
          ]} empty={<EmptyState title="No broker calls yet">Reservations, cancellations and pings sent from here are listed with the kiosk's answer.</EmptyState>} />
        </Section>
      </Stack>
      <Drawer opened={!!open} onClose={() => setOpen(null)} position="right" size="lg" title={<Text fw={800}>{open?.kind} on kiosk {open?.kiosk_id}</Text>}>
        {open && (
          <Stack>
            <Facts cols={2} items={[["Time", dateTime(open.created_at)], ["Kiosk", <KioskLink id={open.kiosk_id} />], ["Reference", open.reference_number ?? "-"],
              ["Result", open.success ? <Status kind="ok">OK</Status> : <Status kind="bad">Failed</Status>]]} />
            {open.error && <Alert color="red" variant="light">{open.error}</Alert>}
            <Text fw={700} size="sm">Request</Text><JsonBlock value={open.request} mah={320} />
            <Text fw={700} size="sm">Answer</Text><JsonBlock value={open.result} mah={320} />
          </Stack>
        )}
      </Drawer>
      <Drawer opened={!!kiosk} onClose={() => setKiosk(null)} position="right" size="lg" title={<Text fw={800}>Kiosk {kiosk?.kiosk_id} on the broker</Text>}>
        {kiosk && (
          <Stack>
            <Facts cols={2} items={[["Connection", <Connection k={kiosk} />], ["Host", kiosk.host || "-"], ["Registered", dateTime(kiosk.registered_at)],
              ["Registrations", num(kiosk.registrations)], ["Disconnected", kiosk.disconnected_at ? dateTime(kiosk.disconnected_at) : "-"], ["Kiosk page", <KioskLink id={kiosk.kiosk_id} />]]} />
            <Text fw={700} size="sm">Version and pricing it registered with</Text>
            <JsonBlock value={kiosk.version} />
          </Stack>
        )}
      </Drawer>
    </>
  );
}
