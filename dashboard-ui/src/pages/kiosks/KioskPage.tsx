import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Anchor, Badge, Button, Code, Drawer, Grid, Group, Menu, Select, SimpleGrid, Skeleton, Stack, Text, TextInput, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconChevronDown, IconPlugConnected, IconAdjustments, IconRefresh, IconClock, IconFileSearch, IconUpload, IconTerminal2, IconPower, IconBuildingStore } from "@tabler/icons-react";
import { api, qs, type Page } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { ago, bytes, dateTime, num, short } from "@/lib/format";
import { confirm, notifyOk } from "@/lib/notify";
import { EmptyState, ErrorState, LocalTable, ServerTable, useUrlSort } from "@/components/DataTable";
import { Facts, PageHeader, RouteTabs, Section, Status } from "@/components/ui";
import { CommandDialog } from "@/components/CommandDialog";
import { ReprovisionButton } from "@/pages/provisioning/Reprovision";
import { engineVersion } from "./KiosksPage";

type Detail = any; // the shapes are the API's (server.py kiosk_detail); typed where used
const FS_STATE = ["Active", "InProgress", "NeedsDependency", "Error"];
const FILE_TYPES = ["UpdateClientServiceLogs", "KioskClientServiceLogs", "KioskEngineLogs", "UpdateManagerLogs", "DeviceServiceLogs", "HALLogs", "ErrorLogs", "Configuration", "KioskEngineDataDirectory", "Certificates"];

export function KioskPage() {
  const { id = "", tab = "overview" } = useParams();
  const can = useCan();
  const d = useQuery({ queryKey: ["kiosks", "one", id], queryFn: () => api<Detail>(`/kiosks/${id}`) });
  const a = useQuery({ queryKey: ["kiosks", "activity", id], queryFn: () => api<Detail>(`/kiosks/${id}/activity`) });
  const [cmd, setCmd] = useState<string | null>(null);
  const [peek, setPeek] = useState<"peek" | "upload" | null>(null);
  const send = useMutation({
    mutationFn: async (v: { command: string; payload?: unknown; version?: number; wait?: number }) => {
      const t = (await api<Record<string, { version: number; payload: unknown }>>("/jobs/commands"))[v.command] || { version: 1, payload: null };
      return api<{ delivered: number; response?: unknown }>("/jobs/iot", { body: { target: id, command: v.command, version: v.version || t.version, payload: v.payload === undefined ? t.payload : v.payload, wait: v.wait || 0 } });
    },
    onSuccess: (r, v) => notifyOk(`${v.command}: ${r.delivered ? "delivered" : "not delivered (the kiosk is not connected)"}${r.response !== undefined ? (r.response ? ", the kiosk answered" : ", no answer") : ""}`),
  });
  if (d.isError) return <><PageHeader title={`Kiosk ${id}`} crumbs={[{ label: "Kiosks", to: "/kiosks" }]} /><ErrorState error={d.error} onRetry={() => d.refetch()} /></>;
  const k = d.data?.kiosk, loc = d.data?.location || {};
  const seen = k ? Date.now() - new Date(k.last_seen_at).getTime() : 0;
  const status = !k ? null : d.data.mqtt ? <Status kind="ok">Online</Status> : seen < 20 * 60e3 ? <Status kind="ok">Recent</Status> : seen < 864e5 ? <Status kind="warn">Stale</Status> : <Status kind="bad">Offline</Status>;
  const reboot = async () => {
    if (await confirm({ title: `Reboot kiosk ${id}?`, danger: true, confirmLabel: "Reboot", body: "Anyone using the kiosk is interrupted. It comes back in a few minutes." }))
      send.mutate({ command: "ExecuteHttpRequest", version: 2, payload: { Url: "http://localhost:6001/api/kernel/shutdown?shutdownType=Reboot", HttpMethod: "POST", JsonBody: "" } });
  };
  return (
    <>
      <PageHeader crumbs={[{ label: "Kiosks", to: "/kiosks" }]}
        title={<Group gap="sm">{`Kiosk ${id}`}{loc.label || loc.city ? <Text span fz={22} fw={600}>{loc.label || loc.city}</Text> : null}</Group>}
        description={k ? <Group gap="lg" component="span">{status}<span>{k.market ? `${k.market} market` : "no market"}</span>
          {engineVersion(k.info?.Versions) && <span>Kiosk Engine {engineVersion(k.info.Versions)}</span>}
          <Anchor component={Link} to={`/stores/stores?q=${id}`} fw={700} size="sm"><IconBuildingStore size={14} style={{ verticalAlign: -2 }} /> Store {id}</Anchor>
          {d.data.mqtt && <span>MQTT from {d.data.mqtt.peer}</span>}</Group> : <Skeleton h={16} w={360} component="span" display="inline-block" />}
        actions={can() && <>
          <Button variant="default" leftSection={<IconPlugConnected size={16} />} loading={send.isPending && send.variables?.command === "Ping"} onClick={() => send.mutate({ command: "Ping", wait: 10 })}>Ping</Button>
          <Button variant="default" leftSection={<IconAdjustments size={16} />} onClick={() => send.mutate({ command: "TriggerGetConfigChanges" })}>Push config</Button>
          <Menu position="bottom-end" withArrow>
            <Menu.Target><Button rightSection={<IconChevronDown size={16} />}>More</Button></Menu.Target>
            <Menu.Dropdown>
              <Menu.Item leftSection={<IconRefresh size={16} />} onClick={() => send.mutate({ command: "TriggerReportFileSetVersions" })}>Check file sets</Menu.Item>
              <Menu.Item leftSection={<IconClock size={16} />} onClick={() => send.mutate({ command: "SyncTimestampAndTimezone" })}>Sync clock</Menu.Item>
              <Menu.Item leftSection={<IconFileSearch size={16} />} onClick={() => setPeek("peek")}>Peek files...</Menu.Item>
              <Menu.Item leftSection={<IconUpload size={16} />} onClick={() => setPeek("upload")}>Upload files...</Menu.Item>
              <Menu.Item leftSection={<IconTerminal2 size={16} />} onClick={() => setCmd("Ping")}>Other command...</Menu.Item>
              <Menu.Divider />
              <Menu.Item color="red" leftSection={<IconPower size={16} />} onClick={reboot}>Reboot...</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </>} />
      <RouteTabs base={`/kiosks/${id}`} value={tab} tabs={["overview", "software", "inventory", "rentals", "reservations", "commands", "files", "events", "settings"]
        .map((t) => ({ value: t, label: t[0].toUpperCase() + t.slice(1) }))} />
      {d.isPending ? <Stack><Skeleton h={90} /><Skeleton h={260} /></Stack> : (
        <>
          {tab === "overview" && <Overview d={d.data} a={a.data} />}
          {tab === "software" && <Software d={d.data} />}
          {tab === "inventory" && <Inventory id={id} a={a.data} />}
          {tab === "rentals" && <Rentals id={id} />}
          {tab === "reservations" && <LocalTable rows={a.data?.reservations} loading={a.isPending} rowKey={(r: any) => r.reference_number}
            columns={[{ id: "ref", header: "Reference", value: (r: any) => r.reference_number, cell: (r: any) => <Text fw={700} size="sm">{r.reference_number}</Text> },
              { id: "status", header: "Status", value: (r: any) => r.status, cell: (r: any) => r.status },
              { id: "titles", header: "Titles", cell: (r: any) => (r.items || []).map((x: any) => x.TitleID).join(", ") },
              { id: "created", header: "Created", value: (r: any) => r.created_at, cell: (r: any) => dateTime(r.created_at) },
              { id: "expires", header: "Expires", value: (r: any) => r.expires_at, cell: (r: any) => dateTime(r.expires_at) }]}
            empty={<EmptyState title="No reservations at this kiosk">Send one from the Reservation broker page.</EmptyState>} />}
          {tab === "commands" && <Section title="Command history" actions={can() && <Button size="xs" onClick={() => setCmd("Ping")}>New command</Button>}>
            {a.isPending ? <Skeleton h={200} /> : a.data?.requests?.length ? <div className="rb-console">{a.data.requests.map((r: any) => (
              <div key={r.request_id}><span style={{ color: "#ff8fa3" }}>›</span> {r.command} <span style={{ color: "#cfc6d6" }}>v{r.version} · {dateTime(r.created_at)}</span>
                <div style={{ color: r.responded_at ? "#6ee7b7" : "#fcd34d" }}>{r.responded_at ? `✓ ${JSON.stringify(r.response).slice(0, 240)}` : "… no answer"}</div></div>))}</div>
              : <EmptyState title="No commands sent to this kiosk yet" />}
          </Section>}
          {tab === "files" && <Files id={id} a={a.data} onPeek={() => setPeek("peek")} onUpload={() => setPeek("upload")} />}
          {tab === "events" && <LocalTable rows={d.data.events} rowKey={(r: any) => r.id} initialSort={{ sort: "time", dir: "desc" }}
            columns={[{ id: "time", header: "Time", value: (r: any) => r.created_at, cell: (r: any) => dateTime(r.created_at) },
              { id: "source", header: "Source", value: (r: any) => r.source, cell: (r: any) => r.source },
              { id: "kind", header: "Kind", value: (r: any) => r.kind, cell: (r: any) => r.kind },
              { id: "payload", header: "Details", width: 520, cell: (r: any) => <Text size="xs" ff="monospace" lineClamp={2}>{r.payload ? JSON.stringify(r.payload) : ""}</Text> }]}
            empty={<EmptyState title="No events" />} />}
          {tab === "settings" && <Settings id={id} d={d.data} a={a.data} />}
        </>
      )}
      <CommandDialog kiosks={[id]} preset={cmd || undefined} opened={!!cmd} onClose={() => setCmd(null)} />
      <FilesDialog id={id} mode={peek} onClose={() => setPeek(null)} />
    </>
  );
}

function Overview({ d, a }: { d: Detail; a: Detail }) {
  const k = d.kiosk;
  const today = a?.daily?.length ? a.daily[a.daily.length - 1].n : 0;
  return (
    <Stack>
      <Section><Facts cols={5} items={[
        ["Last seen", ago(k.last_seen_at)],
        ["Configuration", d.config ? `v${d.config.activated_version}${d.config.activated_version >= d.config_current ? ", current" : `, current is v${d.config_current}`}` : "never pulled"],
        ["Discs", a ? `${num(a.inventory?.discs)} in kiosk · ${num(a.out)} out` : <Skeleton h={14} w={120} />],
        ["Rentals today", a ? num(today) : <Skeleton h={14} w={40} />],
        ["Update Service", d.store ? ago(d.store.last_check_in) : "never"],
      ]} /></Section>
      <Grid>
        <Grid.Col span={{ base: 12, lg: 7 }}><Section title="Software" p={0}><RepoTable rows={d.repositories.filter((r: any) => r.reported).slice(0, 10)} /></Section></Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}><Section title="Recent activity" p={0}>
          <LocalTable rows={d.events.slice(0, 10)} rowKey={(r: any) => r.id} sortable={false}
            columns={[{ id: "t", header: "When", cell: (r: any) => ago(r.created_at) }, { id: "k", header: "What", cell: (r: any) => `${r.source} · ${r.kind}` }]}
            empty={<EmptyState title="Nothing yet" />} /></Section></Grid.Col>
      </Grid>
    </Stack>
  );
}

function RepoTable({ rows }: { rows: any[] }) {
  return <LocalTable rows={rows} rowKey={(r: any) => r.repo} initialSort={{ sort: "repo", dir: "asc" }}
    columns={[{ id: "repo", header: "Repository", value: (r: any) => r.repo, cell: (r: any) => <Text fw={700} size="sm">{r.repo}</Text> },
      { id: "runs", header: "Runs", value: (r: any) => r.reported_label || r.reported, cell: (r: any) => r.reported ? <Code>{r.reported_label || short(r.reported)}</Code> : "-" },
      { id: "server", header: "Newest on the server", cell: (r: any) => <Code>{short(r.server_head)}</Code> },
      { id: "state", header: "", value: (r: any) => (r.current ? 1 : 0), cell: (r: any) => !r.subscribed ? <Status kind="off">Not subscribed</Status> : !r.reported ? <Status kind="warn">No report</Status>
        : r.current ? <Status kind="ok">Current</Status> : <Status kind="warn">Behind</Status> }]}
    empty={<EmptyState title="No repository reported yet">The kiosk reports its Update Service repositories on its next poll.</EmptyState>} />;
}

function Software({ d }: { d: Detail }) {
  const v = d.kiosk.info?.Versions;
  return (
    <Stack>
      <Section title={<Group gap="xs"><Text fw={700}>Update Service repositories</Text><Anchor component={Link} to="/releases" size="sm">Releases</Anchor></Group>} p={0}><RepoTable rows={d.repositories} /></Section>
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Section title="File sets" p={0}><LocalTable rows={d.filesets} rowKey={(r: any) => `${r.channel}${r.fileset_id}`}
          columns={[{ id: "set", header: "Set", value: (r: any) => r.fileset_id, cell: (r: any) => `${r.fileset_id} ${r.name || ""}` },
            { id: "ch", header: "Channel", value: (r: any) => r.channel, cell: (r: any) => r.channel }, { id: "ver", header: "Version", value: (r: any) => r.version, cell: (r: any) => r.version || "-" },
            { id: "state", header: "State", value: (r: any) => r.state, cell: (r: any) => r.state === 0 ? <Status kind="ok">Active</Status> : <Status kind={r.state === 1 ? "warn" : "bad"}>{FS_STATE[r.state] || r.state}</Status> },
            { id: "at", header: "Reported", value: (r: any) => r.updated_at, cell: (r: any) => ago(r.updated_at) }]}
          empty={<EmptyState title="No file-set report" />} /></Section>
        <Section title="Versions the Kiosk Client reports">{v ? <Facts cols={2} items={Object.entries(v).map(([n, x]) => [n, String(x)])} /> : <Text size="sm">No report yet.</Text>}
          <Text size="sm" mt="md">Update Client {d.kiosk.ucs_version || "-"}{d.registration?.last_ping_at ? `, last ping ${ago(d.registration.last_ping_at)}` : ""}</Text></Section>
      </SimpleGrid>
      <Section title="Deployments for this kiosk" p={0}><LocalTable rows={d.deployments} rowKey={(r: any) => r.id}
        columns={[{ id: "id", header: "Deployment", value: (r: any) => r.id, cell: (r: any) => r.id }, { id: "fs", header: "File set", value: (r: any) => r.fileset_id, cell: (r: any) => r.fileset_id },
          { id: "rev", header: "Revision", value: (r: any) => r.revision_id, cell: (r: any) => r.revision_id ?? "remove" }, { id: "ch", header: "Channel", value: (r: any) => r.channel, cell: (r: any) => r.channel },
          { id: "tgt", header: "Target", value: (r: any) => r.target_type, cell: (r: any) => r.target_type }, { id: "at", header: "Created", value: (r: any) => r.created_at, cell: (r: any) => dateTime(r.created_at) }]}
        empty={<EmptyState title="No deployment applies to this kiosk" />} /></Section>
    </Stack>
  );
}

function Inventory({ id, a }: { id: string; a: Detail }) {
  const [sort, setSort] = useUrlSort({ sort: "slot", dir: "asc" });
  return (
    <Stack>
      <Section><Facts items={[["Discs", num(a?.inventory?.discs)], ["Titles", num(a?.inventory?.titles)], ["Rented out from here", num(a?.out)], ["Last inventory sync", ago(a?.inventory?.last_sync)]]} /></Section>
      <ServerTable<any> queryKey={["kiosks", "inventory", id]} sort={sort} onSort={setSort} rowKey={(r) => r.barcode} height="calc(100vh - 430px)"
        fetchPage={async (p) => { const d = await api<any>(`/tables/inventory` + qs({ kiosk_id: id, ...p })); return { rows: d.rows, total: d.total }; }}
        columns={[{ id: "barcode", header: "Barcode", sort: "barcode", cell: (r) => r.barcode }, { id: "deck", header: "Deck", sort: "deck", cell: (r) => r.deck },
          { id: "slot", header: "Slot", sort: "slot", cell: (r) => r.slot }, { id: "status", header: "Status", sort: "status", cell: (r) => r.status },
          { id: "at", header: "Updated", sort: "updated_at", cell: (r) => dateTime(r.updated_at) }]}
        empty={<EmptyState title="No inventory reported">The kiosk sends its discs with its next inventory sync.</EmptyState>} />
    </Stack>
  );
}

function Rentals({ id }: { id: string }) {
  const [sort, setSort] = useUrlSort({ sort: "when", dir: "desc" });
  return <ServerTable<any> queryKey={["kiosks", "rentals", id]} sort={sort} onSort={setSort} rowKey={(r) => r.id}
    fetchPage={(p) => api<Page<any>>("/rentals" + qs({ kind: "rentals", kiosk_id: id, ...p }))}
    columns={[{ id: "when", header: "Rented", sort: "when", cell: (r) => dateTime(r.rented_at) }, { id: "title", header: "Title", sort: "title", cell: (r) => r.title || `product ${r.product_id}` },
      { id: "barcode", header: "Barcode", sort: "barcode", cell: (r) => r.barcode }, { id: "status", header: "Status", sort: "status", cell: (r) => r.status === "out" ? <Status kind="warn">Out</Status> : r.status },
      { id: "ret", header: "Returned", sort: "returned_at", cell: (r) => (r.returned_at ? `${ago(r.returned_at)}${r.return_kiosk_id && r.return_kiosk_id !== r.kiosk_id ? ` to ${r.return_kiosk_id}` : ""}` : "") }]}
    empty={<EmptyState title="No rentals at this kiosk yet" />} />;
}

function Files({ id, a, onPeek, onUpload }: { id: string; a: Detail; onPeek: () => void; onUpload: () => void }) {
  const can = useCan();
  return (
    <Section title="Files uploaded from this kiosk" actions={can() && <><Button size="xs" variant="default" onClick={onPeek}>Peek files</Button><Button size="xs" onClick={onUpload}>Upload files</Button></>} p={0}>
      <LocalTable rows={a?.uploads} loading={!a} rowKey={(r: any) => r.key} initialSort={{ sort: "when", dir: "desc" }}
        columns={[{ id: "file", header: "File", value: (r: any) => r.key, cell: (r: any) => <Anchor href={`/api/uploads/file?key=${encodeURIComponent(r.key)}`} size="sm" fw={600}>{r.key.split("/").pop()}</Anchor> },
          { id: "size", header: "Size", align: "right", value: (r: any) => r.size, cell: (r: any) => bytes(r.size) },
          { id: "when", header: "When", value: (r: any) => r.modified, cell: (r: any) => dateTime(r.modified) }]}
        empty={<EmptyState title={`Nothing uploaded from kiosk ${id} yet`}>Use Upload files to fetch logs or other files from the kiosk.</EmptyState>} />
    </Section>
  );
}

function FilesDialog({ id, mode, onClose }: { id: string; mode: "peek" | "upload" | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [type, setType] = useState<string | null>(FILE_TYPES[0]);
  const [path, setPath] = useState("");
  const [names, setNames] = useState("");
  const run = useMutation({
    mutationFn: () => mode === "peek"
      ? api<any>("/jobs/peek", { body: { kiosk_id: id, fileType: type, path: path || undefined } })
      : api<any>("/jobs/upload", { body: { kiosk_id: id, fileType: type, fileNames: names.split(",").map((s) => s.trim()).filter(Boolean), zip: true } }),
    onSuccess: () => { if (mode === "upload") qc.invalidateQueries({ queryKey: ["kiosks", "activity", id] }); },
  });
  useEffect(() => { run.reset(); }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Drawer opened={!!mode} onClose={onClose} position="right" size="lg" title={<Title order={3}>{mode === "peek" ? `Peek files on kiosk ${id}` : `Upload files from kiosk ${id}`}</Title>}>
      <Stack>
        <Select label="File type" data={FILE_TYPES} value={type} onChange={setType} />
        {mode === "peek" ? <TextInput label="Or any folder on the kiosk" placeholder="C:\ProgramData\Redbox" value={path} onChange={(e) => setPath(e.currentTarget.value)} />
          : <TextInput label="File names" description="Comma separated, as Peek lists them; empty uploads all" value={names} onChange={(e) => setNames(e.currentTarget.value)} />}
        <Group><Button onClick={() => run.mutate()} loading={run.isPending}>{mode === "peek" ? "Peek" : "Upload"}</Button>
          {run.isPending && <Text size="sm">Waiting for the kiosk (up to a minute)...</Text>}</Group>
        {run.data && <><Code block style={{ maxHeight: 380, overflow: "auto" }}>{JSON.stringify(run.data.response, null, 2) || "No answer: is the kiosk connected?"}</Code>
          {mode === "upload" && run.data.s3Key && <Badge variant="light">Stored as {run.data.s3Key}</Badge>}</>}
      </Stack>
    </Drawer>
  );
}

function Settings({ id, d, a }: { id: string; d: Detail; a: Detail }) {
  const can = useCan();
  const qc = useQueryClient();
  const k = d.kiosk, loc = d.location || {};
  const init = { market: k.market || "", banner: k.banner || "", timezone: k.timezone || "", state: k.state || "", label: loc.label || "" };
  const form = useForm({ initialValues: init });
  useEffect(() => { form.setValues(init); form.resetDirty(init); }, [d]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: (v: typeof init) => api(`/kiosks/${id}`, { method: "PUT", body: { kiosk: { market: v.market, banner: v.banner, timezone: v.timezone, state: v.state }, location: { label: v.label } } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["kiosks"] }); notifyOk("Saved"); },
  });
  const revoke = useMutation({
    mutationFn: (cid: string) => api("/provisioning/certificates/revoke", { body: { certificate_id: cid } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["kiosks", "activity", id] }); notifyOk("Certificate revoked"); },
  });
  return (
    <SimpleGrid cols={{ base: 1, lg: 2 }}>
      <Section title="Kiosk">
        <form onSubmit={form.onSubmit((v) => save.mutate(v))}>
          <Stack>
            <Text size="sm">The address comes from <Anchor component={Link} to={`/stores/stores?q=${id}`} fw={700}>store {id}</Anchor>; edit it there.</Text>
            <TextInput label="Name / label" disabled={!can()} {...form.getInputProps("label")} />
            <TextInput label="Market" disabled={!can()} {...form.getInputProps("market")} />
            <TextInput label="Banner" disabled={!can()} {...form.getInputProps("banner")} />
            <TextInput label="Time zone" description="Windows time zone name, e.g. Eastern Standard Time" disabled={!can()} {...form.getInputProps("timezone")} />
            {can() && <Group justify="flex-end"><Button type="submit" loading={save.isPending} disabled={!form.isDirty()}>Save changes</Button></Group>}
          </Stack>
        </form>
      </Section>
      <Stack>
        <Section title="Provisioning"><Text size="sm" mb="sm">Files for setting up or re-pointing this kiosk.</Text>
          <Group><ReprovisionButton kioskId={Number(id)} market={k.market || undefined} />
            <Button component="a" variant="default" href={`/api/provisioning/installer/${id}.json`}>Installer settings.json</Button>
            <Button component="a" variant="default" href="/api/provisioning/ca.crt">Backend CA</Button></Group></Section>
        <Section title="IoT certificates" p={0}><LocalTable rows={a?.certificates} loading={!a} rowKey={(r: any) => r.certificate_id}
          columns={[{ id: "id", header: "Certificate", cell: (r: any) => <Code>{short(r.certificate_id)}</Code> },
            { id: "at", header: "Issued", value: (r: any) => r.created_at, cell: (r: any) => dateTime(r.created_at) },
            { id: "st", header: "", cell: (r: any) => r.revoked_at ? <Status kind="off">Revoked</Status> : can()
              ? <Button size="xs" color="red" variant="light" loading={revoke.isPending && revoke.variables === r.certificate_id}
                  onClick={async () => { if (await confirm({ title: "Revoke this certificate?", danger: true, confirmLabel: "Revoke", body: "The kiosk's MQTT connection is refused until it asks for a new certificate." })) revoke.mutate(r.certificate_id); }}>Revoke</Button>
              : <Status kind="ok">Active</Status> }]}
          empty={<EmptyState title="No certificate issued yet" />} /></Section>
      </Stack>
    </SimpleGrid>
  );
}
