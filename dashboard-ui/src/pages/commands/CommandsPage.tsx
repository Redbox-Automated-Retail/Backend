import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Anchor, Autocomplete, Badge, Button, Code, Drawer, FileInput, Group, JsonInput, NumberInput, Select, SimpleGrid, Stack, Text, Textarea, TextInput,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconDownload, IconFileUpload, IconRefresh, IconSend } from "@tabler/icons-react";
import { LocalTable, EmptyState } from "@/components/DataTable";
import { Facts, PageHeader, Section, StatCards, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { notifyOk } from "@/lib/notify";
import { bytes, dateTime, num } from "@/lib/format";
import { JsonBlock, KioskLink, jsonPreview } from "../logs/filters";

type Templates = Record<string, { version: number; payload: unknown }>;
type Client = { clientId: string; peer: string; subscriptions: string[] };
type IotRequest = { request_id: string; kiosk_id: number | null; topic: string; command: string; version: number; payload: unknown; response: unknown; created_at: string; responded_at: string | null };
type DownloadJob = { job_id: string; metadata: { Name?: string; [k: string]: unknown }; target: string; created_at: string; kiosk_id: number | null; status: string | null; updated_at: string | null };
type LegacyDownload = { id: number; number: string | null; name: string; type: number; file_name: string | null; destination_path: string; created_at: string; sent_to: string[] | null };
type Requests = { requests: IotRequest[]; download_jobs: DownloadJob[]; legacy_downloads: LegacyDownload[] };
type Upload = { key: string; size: number; modified: string };

const CUSTOM = "_custom";
const targetHint = "A kiosk id, market:Name, or all";
const isTarget = (v: string) => v === "all" || /^\d+$/.test(v) || /^market:.+/.test(v);

// --------------------------------------------------------------------------------------- IoT command

const iotSchema = z.object({
  target: z.string().trim().refine(isTarget, targetHint),
  command: z.string().min(1, "Pick a command"),
  custom: z.string(),
  version: z.number({ invalid_type_error: "A version number" }).int().min(1, "1 or more"),
  payload: z.string().refine((v) => { if (!v.trim()) return true; try { JSON.parse(v); return true; } catch { return false; } }, "Not valid JSON"),
  wait: z.number({ invalid_type_error: "Seconds" }).min(0).max(120, "At most 120 s"),
}).refine((v) => v.command !== CUSTOM || /^\w+$/.test(v.custom.trim()), { path: ["custom"], message: "Enter the command name" });

function IotForm({ templates, targets }: { templates: Templates; targets: string[] }) {
  const qc = useQueryClient();
  const [out, setOut] = useState<unknown>(null);
  const names = Object.keys(templates);
  const form = useForm({
    initialValues: { target: targets[0] || "all", command: names[0] || CUSTOM, custom: "", version: 2 as number | "", payload: "", wait: 10 as number | "" },
    validate: zodResolver(iotSchema),
  });
  const fill = (cmd: string) => {
    const t = templates[cmd];
    form.setFieldValue("command", cmd);
    if (t) {
      form.setFieldValue("version", t.version);
      form.setFieldValue("payload", t.payload === null || t.payload === undefined ? "" : JSON.stringify(t.payload, null, 2));
    }
  };
  useEffect(() => { if (names[0]) fill(form.values.command); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const send = useMutation({
    mutationFn: (v: typeof form.values) => api<{ requestId: string; topic: string; delivered: number; response?: unknown }>("/jobs/iot", { body: {
      target: v.target.trim(), command: v.command === CUSTOM ? v.custom.trim() : v.command, version: v.version, payload: v.payload, wait: v.wait,
    } }),
    onSuccess: (x) => { setOut(x); notifyOk(`Sent to ${x.topic} (${x.delivered} ${x.delivered === 1 ? "delivery" : "deliveries"})`); qc.invalidateQueries({ queryKey: ["jobs-requests"] }); },
  });
  const wait = Number(form.values.wait || 0);
  return (
    <Section title={<Group gap="xs"><Text fw={700}>Send an IoT command</Text><Badge variant="light" color={targets.length ? "teal" : "gray"}>{targets.length} connected</Badge></Group>}>
      <form onSubmit={form.onSubmit((v) => { setOut(null); send.mutate(v); })}>
        <Stack>
          <Autocomplete label="Target" description={targetHint} data={["all", ...targets]} {...form.getInputProps("target")} />
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Select label="Command" searchable allowDeselect={false} data={[...names.map((n) => ({ value: n, label: n })), { value: CUSTOM, label: "Other command..." }]}
              value={form.values.command} onChange={(v) => v && fill(v)} error={form.errors.command} />
            <NumberInput label="Version" min={1} allowDecimal={false} {...form.getInputProps("version")} />
          </SimpleGrid>
          {form.values.command === CUSTOM && <TextInput label="Command name" placeholder="e.g. CancelDownloadFileJob" {...form.getInputProps("custom")} />}
          <JsonInput label="Payload (JSON)" description="Filled from the command's template; empty sends no payload" autosize minRows={3} maxRows={14} formatOnBlur
            validationError="Not valid JSON" {...form.getInputProps("payload")} />
          <NumberInput label="Wait for the answer" description="Seconds; 0 sends without waiting" suffix=" s" min={0} max={120} w={220} {...form.getInputProps("wait")} />
          <Text size="sm" c="dark.1">
            ExecuteHttpRequest runs an HTTP call on the kiosk, e.g. POST <Code>http://localhost:9001/api/pricing/all/refresh</Code>, <Code>.../api/campaign/refresh</Code>,
            or <Code>http://localhost:6001/api/kernel/shutdown?shutdownType=Reboot</Code>.
          </Text>
          <Group>
            <Button type="submit" leftSection={<IconSend size={16} />} loading={send.isPending}>Send</Button>
            {send.isPending && wait > 0 && <Text size="sm">Waiting up to {wait} s for the answer...</Text>}
          </Group>
          {out != null && <JsonBlock value={out} mah={360} />}
        </Stack>
      </form>
    </Section>
  );
}

// ----------------------------------------------------------------------------------- download jobs

const dlSchema = z.object({
  target: z.string().trim().refine(isTarget, targetHint),
  name: z.string().trim().min(1, "Name the job"),
  destination_path: z.string(),
  file: z.instanceof(File).nullable(),
  script: z.instanceof(File).nullable().refine((f) => !f || f.name.toLowerCase().endsWith(".ps1"), "The Update Client only runs .ps1 scripts"),
}).refine((v) => v.file || v.script, { path: ["file"], message: "Choose a file, a script, or both" });

function DownloadJobForm({ targets }: { targets: string[] }) {
  const qc = useQueryClient();
  const form = useForm<{ target: string; name: string; destination_path: string; file: File | null; script: File | null }>({
    initialValues: { target: "all", name: "", destination_path: "", file: null, script: null }, validate: zodResolver(dlSchema),
  });
  const send = useMutation({
    mutationFn: (v: typeof form.values) => {
      const fd = new FormData();
      fd.set("target", v.target.trim()); fd.set("name", v.name.trim()); fd.set("destination_path", v.destination_path);
      if (v.file) fd.set("file", v.file);
      if (v.script) fd.set("script", v.script);
      return api<{ delivered?: number }>("/jobs/download", { form: fd });
    },
    onSuccess: (x) => { notifyOk(`Job sent (${x?.delivered ?? 0} deliveries)`); form.reset(); qc.invalidateQueries({ queryKey: ["jobs-requests"] }); },
  });
  return (
    <Section title="Download-file job (Update Client)">
      <form onSubmit={form.onSubmit((v) => send.mutate(v))}>
        <Stack>
          <Text size="sm" c="dark.1">Sends ExecuteDownloadFileJob: the kiosk downloads the file, then runs the PowerShell script if there is one.</Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Autocomplete label="Target" description={targetHint} data={["all", ...targets]} {...form.getInputProps("target")} />
            <TextInput label="Name" required {...form.getInputProps("name")} />
          </SimpleGrid>
          <TextInput label="Destination folder on the kiosk" placeholder="C:\Temp" {...form.getInputProps("destination_path")} />
          <FileInput label="File" placeholder="Choose a file" clearable leftSection={<IconFileUpload size={16} />} {...form.getInputProps("file")} />
          <FileInput label="PowerShell script (.ps1, optional)" placeholder="Choose a .ps1" accept=".ps1" clearable leftSection={<IconFileUpload size={16} />} {...form.getInputProps("script")} />
          <Group><Button type="submit" loading={send.isPending}>Start job</Button></Group>
        </Stack>
      </form>
    </Section>
  );
}

const legacySchema = z.object({
  store: z.string().trim().refine((v) => !v || /^\d+$/.test(v), "A store number, or empty for every store"),
  name: z.string().trim().min(1, "Name it"),
  destination_path: z.string().trim().min(1, "Where the file goes on the kiosk"),
  file: z.instanceof(File).nullable(),
  lua: z.string(),
}).refine((v) => v.file || v.lua.trim(), { path: ["file"], message: "Choose a file or write a Lua script" });

function LegacyDownloadForm() {
  const qc = useQueryClient();
  const form = useForm<{ store: string; name: string; destination_path: string; file: File | null; lua: string }>({
    initialValues: { store: "", name: "", destination_path: "", file: null, lua: "" }, validate: zodResolver(legacySchema),
  });
  const send = useMutation({
    mutationFn: (v: typeof form.values) => {
      const fd = new FormData();
      fd.set("store", v.store.trim()); fd.set("name", v.name.trim()); fd.set("destination_path", v.destination_path.trim()); fd.set("lua", v.lua);
      if (v.file) fd.set("file", v.file);
      return api<{ id: number; note: string }>("/jobs/legacy-download", { form: fd });
    },
    onSuccess: (x) => { notifyOk(`Queued as #${x.id}: ${x.note}`); form.reset(); qc.invalidateQueries({ queryKey: ["jobs-requests"] }); },
  });
  return (
    <Section title="Download file (updatemgrd)">
      <form onSubmit={form.onSubmit((v) => send.mutate(v))}>
        <Stack>
          <Text size="sm" c="dark.1">Delivered to the Update Manager on the store's next server poll; the Lua script runs after the file lands.</Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput label="Store" description="Empty: every store" inputMode="numeric" {...form.getInputProps("store")} />
            <TextInput label="Name" required {...form.getInputProps("name")} />
          </SimpleGrid>
          <TextInput label="Destination folder" required placeholder="C:\Program Files\Redbox\REDS\Kiosk Engine\data" {...form.getInputProps("destination_path")} />
          <FileInput label="File" description="Optional for a script-only job" placeholder="Choose a file" clearable leftSection={<IconFileUpload size={16} />} {...form.getInputProps("file")} />
          <Textarea label="Lua activation script (optional)" ff="monospace" autosize minRows={3} maxRows={12} placeholder="kernel.log('hello from the backend')" {...form.getInputProps("lua")} />
          <Group><Button type="submit" loading={send.isPending}>Queue</Button></Group>
        </Stack>
      </form>
    </Section>
  );
}

// -------------------------------------------------------------------------------------------- page

export function CommandsPage() {
  const can = useCan()("change");
  const qc = useQueryClient();
  const templates = useQuery({ queryKey: ["jobs-commands"], queryFn: () => api<Templates>("/jobs/commands"), staleTime: Infinity });
  const clients = useQuery({ queryKey: ["jobs-clients"], queryFn: () => api<Client[]>("/jobs/clients") });
  const reqs = useQuery({ queryKey: ["jobs-requests"], queryFn: () => api<Requests>("/jobs/requests") });
  const uploads = useQuery({ queryKey: ["uploads"], queryFn: () => api<Upload[]>("/uploads") });
  const [open, setOpen] = useState<IotRequest | null>(null);
  const targets = (clients.data || []).map((c) => c.clientId).filter((c) => /^\d+$/.test(c)).sort((a, b) => Number(a) - Number(b));
  const r = reqs.data;
  const refresh = () => ["jobs-clients", "jobs-requests", "uploads"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));

  return (
    <>
      <PageHeader title="Remote commands" description="IoT commands over MQTT, file jobs for the Update Client and updatemgrd, and files kiosks uploaded"
        actions={<Button variant="default" leftSection={<IconRefresh size={16} />} onClick={refresh}>Refresh</Button>} />
      <StatCards loading={clients.isPending || reqs.isPending} stats={[
        { label: "Connected MQTT clients", value: num(clients.data?.length), color: clients.data?.length ? "teal.4" : undefined },
        { label: "Recent IoT requests", value: num(r?.requests.length), hint: `${num(r?.requests.filter((x) => x.responded_at).length)} answered` },
        { label: "Download-file jobs", value: num(r?.download_jobs.length) },
        { label: "Files from kiosks", value: num(uploads.data?.length) },
      ]} />
      <Stack gap="lg">
        {can && (
          templates.isPending || clients.isPending ? <Section><Text>Loading the command templates...</Text></Section> : (
            <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="lg">
              <IotForm templates={templates.data || {}} targets={targets} />
              <Stack gap="lg">
                <DownloadJobForm targets={targets} />
                <LegacyDownloadForm />
              </Stack>
            </SimpleGrid>
          )
        )}
        <Section title="Connected MQTT clients" p={0}>
          <LocalTable<Client> rows={clients.data} loading={clients.isPending} error={clients.error} onRetry={() => clients.refetch()} rowKey={(c) => c.clientId} columns={[
            { id: "clientId", header: "Client", value: (c) => c.clientId, cell: (c) => /^\d+$/.test(c.clientId) ? <KioskLink id={c.clientId} /> : <Text size="sm" fw={600}>{c.clientId}</Text> },
            { id: "peer", header: "Address", value: (c) => c.peer, cell: (c) => <Code>{c.peer}</Code> },
            { id: "subs", header: "Subscriptions", value: (c) => c.subscriptions.length, cell: (c) => <Text size="xs" ff="monospace" style={{ whiteSpace: "normal" }}>{c.subscriptions.join(", ")}</Text> },
          ]} empty={<EmptyState title="No kiosk is connected">Update Clients connect to rb-iot on port 8883 with their IoT certificate.</EmptyState>} />
        </Section>
        <Section title="Recent IoT requests" p={0}>
          <LocalTable<IotRequest> rows={r?.requests} loading={reqs.isPending} error={reqs.error} onRetry={() => reqs.refetch()} rowKey={(x) => x.request_id}
            onRowClick={setOpen} maxHeight={520} initialSort={{ sort: "created_at", dir: "desc" }} columns={[
              { id: "created_at", header: "Sent", value: (x) => x.created_at, cell: (x) => <Text size="sm" style={{ whiteSpace: "nowrap" }}>{dateTime(x.created_at)}</Text> },
              { id: "topic", header: "Topic", value: (x) => x.topic, cell: (x) => <Code>{x.topic}</Code> },
              { id: "command", header: "Command", value: (x) => x.command, cell: (x) => <Text size="sm" fw={600}>{x.command}</Text> },
              { id: "version", header: "Version", align: "right", value: (x) => x.version, cell: (x) => x.version },
              { id: "responded_at", header: "Answered", value: (x) => x.responded_at, cell: (x) => x.responded_at ? <Status kind="ok">{dateTime(x.responded_at)}</Status> : <Status kind="off">no answer</Status> },
              { id: "response", header: "Answer", cell: (x) => <Text size="xs" ff="monospace" truncate="end" maw={340}>{jsonPreview(x.response, 140)}</Text> },
            ]} empty={<EmptyState title="No IoT requests yet">Commands sent from here or by the services are listed with the kiosk's answer.</EmptyState>} />
        </Section>
        <SimpleGrid cols={{ base: 1, xl: 2 }} spacing="lg">
          <Section title="Download-file jobs" p={0}>
            <LocalTable<DownloadJob> rows={r?.download_jobs} loading={reqs.isPending} rowKey={(x) => `${x.job_id}/${x.kiosk_id}`} maxHeight={420} initialSort={{ sort: "created_at", dir: "desc" }} columns={[
              { id: "created_at", header: "Created", value: (x) => x.created_at, cell: (x) => dateTime(x.created_at) },
              { id: "name", header: "Job", value: (x) => x.metadata?.Name || "", cell: (x) => <Text size="sm" fw={600}>{x.metadata?.Name || x.job_id}</Text> },
              { id: "target", header: "Target", value: (x) => x.target, cell: (x) => x.target },
              { id: "kiosk_id", header: "Kiosk", value: (x) => x.kiosk_id, cell: (x) => <KioskLink id={x.kiosk_id} /> },
              { id: "status", header: "Status", value: (x) => x.status, cell: (x) => x.status || "-" },
              { id: "updated_at", header: "Updated", value: (x) => x.updated_at, cell: (x) => dateTime(x.updated_at) || "-" },
            ]} empty={<EmptyState title="No download-file jobs">Start one above; kiosks report their progress here.</EmptyState>} />
          </Section>
          <Section title="updatemgrd download files" p={0}>
            <LocalTable<LegacyDownload> rows={r?.legacy_downloads} loading={reqs.isPending} rowKey={(x) => x.id} maxHeight={420} initialSort={{ sort: "id", dir: "desc" }} columns={[
              { id: "id", header: "Id", align: "right", value: (x) => x.id, cell: (x) => x.id },
              { id: "name", header: "Name", value: (x) => x.name, cell: (x) => <Text size="sm" fw={600}>{x.name}</Text> },
              { id: "number", header: "Store", value: (x) => x.number, cell: (x) => x.number || "every store" },
              { id: "file_name", header: "File", value: (x) => x.file_name, cell: (x) => x.file_name || "(script only)" },
              { id: "destination_path", header: "Destination", value: (x) => x.destination_path, cell: (x) => <Text size="xs" ff="monospace" style={{ whiteSpace: "normal", wordBreak: "break-all" }}>{x.destination_path}</Text> },
              { id: "sent_to", header: "Sent to", value: (x) => (x.sent_to || []).length, cell: (x) => (x.sent_to?.length ? x.sent_to.join(", ") : "not yet") },
              { id: "created_at", header: "Created", value: (x) => x.created_at, cell: (x) => dateTime(x.created_at) },
            ]} empty={<EmptyState title="No updatemgrd download files">Queue one above; stores pick it up on their next poll.</EmptyState>} />
          </Section>
        </SimpleGrid>
        <Section title="Files uploaded by kiosks" p={0}>
          <LocalTable<Upload> rows={uploads.data} loading={uploads.isPending} error={uploads.error} onRetry={() => uploads.refetch()} rowKey={(u) => u.key}
            maxHeight={480} initialSort={{ sort: "modified", dir: "desc" }} columns={[
              { id: "key", header: "File", value: (u) => u.key, cell: (u) => (
                <Anchor href={`/api/uploads/file?key=${encodeURIComponent(u.key)}`} size="sm" fw={600} style={{ wordBreak: "break-all" }}>
                  <IconDownload size={14} style={{ verticalAlign: -2, marginRight: 4 }} />{u.key}
                </Anchor>
              ) },
              { id: "size", header: "Size", align: "right", value: (u) => u.size, cell: (u) => bytes(u.size) },
              { id: "modified", header: "Uploaded", value: (u) => u.modified, cell: (u) => dateTime(u.modified) },
            ]} empty={<EmptyState title="No uploads yet">Use Upload logs on a kiosk's page; the files the kiosk sends land here.</EmptyState>} />
        </Section>
      </Stack>
      <Drawer opened={!!open} onClose={() => setOpen(null)} position="right" size="lg" title={<Text fw={800}>{open?.command} v{open?.version}</Text>}>
        {open && (
          <Stack>
            <Facts cols={2} items={[["Sent", dateTime(open.created_at)], ["Answered", open.responded_at ? dateTime(open.responded_at) : "no answer"],
              ["Topic", <Code>{open.topic}</Code>], ["Kiosk", <KioskLink id={open.kiosk_id} />], ["Request id", <Code>{open.request_id}</Code>]]} />
            <Text fw={700} size="sm">Payload</Text><JsonBlock value={open.payload} mah={300} />
            <Text fw={700} size="sm">Answer</Text><JsonBlock value={open.response} mah={400} />
          </Stack>
        )}
      </Drawer>
    </>
  );
}
