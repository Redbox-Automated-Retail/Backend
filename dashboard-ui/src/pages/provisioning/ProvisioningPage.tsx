import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Anchor, Button, Code, Drawer, Group, NumberInput, ScrollArea, Select, SimpleGrid, Skeleton, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconArrowRight, IconCertificate, IconFileDownload } from "@tabler/icons-react";
import { LocalTable, ErrorState, EmptyState } from "@/components/DataTable";
import { Facts, PageHeader, RouteTabs, Section, Status } from "@/components/ui";
import { api, qs } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { ago, dateTime, num, short } from "@/lib/format";
import { KioskLink } from "../logs/filters";
import { ReprovisionButton, useProvisioning, type ProvisioningInfo } from "./Reprovision";

type InstallerKiosk = { id: number; banner_name: string; market_name: string | null; address: string | null; city: string | null; state_name: string; zip: string | null; pending: boolean };
type Cert = { certificate_id: string; kiosk_id: number; market: string | null; created_at: string; revoked_at: string | null };
type Registration = { kiosk_id: number; ucs_version: string | null; assembly_version: string | null; broker_registered: boolean; broker_reason: string | null; registered_at: string; last_ping_at: string | null };
type Session = { guid: string; hash: string | null; version: string | null; log_lines: number; log: unknown[]; created_at: string };

const settingsSchema = z.object({
  id: z.number({ invalid_type_error: "Enter the store ID" }).int().positive("Enter the store ID"),
  kiosk_type: z.enum(["R630", "R620", "R610"]),
  data_zip: z.string().trim().min(1, "Name the data zip").regex(/\.zip$/i, "A .zip file name"),
  test_mode: z.boolean(),
});

function InstallerSettings() {
  const form = useForm({
    initialValues: { id: "" as number | "", kiosk_type: "R630", data_zip: "data.zip", test_mode: false },
    validate: zodResolver(settingsSchema),
  });
  return (
    <Section title="Installer settings">
      <form onSubmit={form.onSubmit((v) => {
        window.location.href = `/api/provisioning/installer/${v.id}.json` + qs({ kiosk_type: v.kiosk_type, data_zip: v.data_zip.trim(), test_mode: v.test_mode ? "true" : "false" });
      })}>
        <Stack>
          <Text size="sm">
            The <Code>settings.json</Code> for <Code>software/installer/cli/Install-Kiosk.ps1</Code>. Put it in the installer payload with the kiosk's
            reprovision file (<Code>kiosk-&lt;id&gt;.json</Code>) and a data zip.
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <NumberInput label="Store ID" required allowDecimal={false} allowNegative={false} hideControls {...form.getInputProps("id")} />
            <Select label="Kiosk type" data={["R630", "R620", "R610"]} allowDeselect={false} {...form.getInputProps("kiosk_type")} />
          </SimpleGrid>
          <TextInput label="Data zip file name" {...form.getInputProps("data_zip")} />
          <Switch label="Test mode" description="For a VM or Windows Sandbox: no reboot" {...form.getInputProps("test_mode", { type: "checkbox" })} />
          <Group>
            <Button type="submit" leftSection={<IconFileDownload size={16} />}>Download settings.json</Button>
            <ReprovisionButton variant="default" />
          </Group>
        </Stack>
      </form>
    </Section>
  );
}

function KiosksTab({ d }: { d?: ProvisioningInfo }) {
  const hosts = Object.values(d?.service_hosts || {});
  return (
    <Stack gap="lg">
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        <InstallerSettings />
        <Section title="Backend address">
          {!d ? <Skeleton h={140} /> : (
            <Stack>
              <Facts cols={1} items={[
                ["Public IP", d.backend_ip || "-"],
                ["Hosts entries", d.host_entries.length ? <Stack gap={2}>{d.host_entries.map((h) => <Code key={h}>{h}</Code>)}</Stack> : "made from the public IP"],
              ]} />
              <Text size="sm">
                Kiosks reach the services by these names: {hosts.length ? hosts.map((h, i) => <span key={h}>{i ? ", " : ""}<Code>{h}</Code></span>) : "see docs/deploy-linux.md"}.
              </Text>
              <Group><Button component="a" href="/api/provisioning/ca.crt" variant="default" leftSection={<IconCertificate size={16} />}>Download the backend CA</Button></Group>
            </Stack>
          )}
        </Section>
      </SimpleGrid>
      <Section title="Stores offered to the kiosk installer" p={0}
        actions={<Anchor component={Link} to="/stores/installer" fw={600} size="sm">Manage in Stores &gt; Kiosk installer <IconArrowRight size={14} style={{ verticalAlign: -2 }} /></Anchor>}>
        <LocalTable<InstallerKiosk> rows={d?.installer_kiosks} loading={!d} rowKey={(r) => r.id} columns={[
          { id: "id", header: "Store", value: (r) => r.id, cell: (r) => <Text fw={700} size="sm">{r.id}</Text>, width: 90 },
          { id: "banner", header: "Banner", value: (r) => r.banner_name, cell: (r) => r.banner_name },
          { id: "market", header: "Market", value: (r) => r.market_name, cell: (r) => r.market_name || "-" },
          { id: "address", header: "Address", value: (r) => [r.address, r.city, r.state_name, r.zip].filter(Boolean).join(", "),
            cell: (r) => [r.address, r.city, r.state_name, r.zip].filter(Boolean).join(", ") },
          { id: "pending", header: "State", align: "right", value: (r) => (r.pending ? 0 : 1), cell: (r) => r.pending ? <Status kind="warn">Pending</Status> : <Status kind="off">Installed</Status> },
        ]} empty={<EmptyState title="No stores offered">Offer stores under Stores &gt; Kiosk installer to list them in the Redbox kiosk installer's store list.</EmptyState>} />
      </Section>
    </Stack>
  );
}

function CertificatesTab({ d }: { d?: ProvisioningInfo }) {
  const can = useCan();
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: (id: string) => api("/provisioning/certificates/revoke", { body: { certificate_id: id } }),
    onSuccess: () => { notifyOk("Certificate revoked"); qc.invalidateQueries({ queryKey: ["provisioning"] }); },
  });
  const ask = async (c: Cert) => {
    if (await confirm({ title: `Revoke kiosk ${c.kiosk_id}'s certificate?`, danger: true, confirmLabel: "Revoke",
      body: "rb-iot refuses this certificate at once and drops the kiosk's MQTT connection; the Update Client asks for a new certificate when it reconnects." }))
      revoke.mutate(c.certificate_id);
  };
  return (
    <Stack gap="lg">
      <Text size="sm" fw={500}>
        Client certificates the Update Client gets from the IoT certificate service for its MQTT connection. Revoking one makes rb-iot refuse it;
        the kiosk asks for a new one when it reconnects.
      </Text>
      <Section title="IoT certificates" p={0}>
        <LocalTable<Cert> rows={d?.certificates} loading={!d} rowKey={(r) => r.certificate_id} initialSort={{ sort: "created_at", dir: "desc" }} columns={[
          { id: "kiosk_id", header: "Kiosk", value: (r) => r.kiosk_id, cell: (r) => <KioskLink id={r.kiosk_id} />, width: 90 },
          { id: "certificate_id", header: "Certificate", value: (r) => r.certificate_id, cell: (r) => <Code title={r.certificate_id}>{short(r.certificate_id)}</Code> },
          { id: "market", header: "Market", value: (r) => r.market, cell: (r) => r.market || "-" },
          { id: "created_at", header: "Issued", value: (r) => r.created_at, cell: (r) => dateTime(r.created_at) },
          { id: "state", header: "State", value: (r) => r.revoked_at || "", cell: (r) => r.revoked_at ? <Status kind="off">Revoked {ago(r.revoked_at)}</Status> : <Status kind="ok">Active</Status> },
          { id: "act", header: "", align: "right", cell: (r) => !r.revoked_at && can() ? (
            <Button size="xs" color="red" variant="light" loading={revoke.isPending && revoke.variables === r.certificate_id} onClick={() => ask(r)}>Revoke</Button>
          ) : null },
        ]} empty={<EmptyState title="No certificates issued yet">Kiosks get one when their Update Client first connects to the IoT certificate service.</EmptyState>} />
      </Section>
      <Section title="Update Client registrations" p={0}>
        <LocalTable<Registration> rows={d?.registrations} loading={!d} rowKey={(r) => r.kiosk_id} columns={[
          { id: "kiosk_id", header: "Kiosk", value: (r) => r.kiosk_id, cell: (r) => <KioskLink id={r.kiosk_id} />, width: 90 },
          { id: "ucs_version", header: "Version", value: (r) => r.ucs_version, cell: (r) => r.ucs_version || "-" },
          { id: "assembly_version", header: "Assembly", value: (r) => r.assembly_version, cell: (r) => r.assembly_version || "-" },
          { id: "broker", header: "Broker", value: (r) => (r.broker_registered ? 1 : 0), cell: (r) => r.broker_registered ? <Status kind="ok">registered</Status> : <Status kind="off">{r.broker_reason || "no"}</Status> },
          { id: "registered_at", header: "Registered", value: (r) => r.registered_at, cell: (r) => dateTime(r.registered_at) },
          { id: "last_ping_at", header: "Last ping", value: (r) => r.last_ping_at, cell: (r) => ago(r.last_ping_at) },
        ]} empty={<EmptyState title="No registrations">Update Clients register when they first connect.</EmptyState>} />
      </Section>
    </Stack>
  );
}

const logLine = (x: unknown) => (typeof x === "string" ? x : JSON.stringify(x));

function InstallerTab({ d }: { d?: ProvisioningInfo }) {
  const [open, setOpen] = useState<Session | null>(null);
  return (
    <Stack gap="lg">
      <Text size="sm" fw={500}>Sessions of the original Redbox store installer against the Update Service (it downloads the store's data zip and reports progress). Click one to read its log.</Text>
      <Section p={0}>
        <LocalTable<Session> rows={d?.installer_sessions} loading={!d} rowKey={(r) => r.guid} onRowClick={setOpen} initialSort={{ sort: "created_at", dir: "desc" }} columns={[
          { id: "created_at", header: "Started", value: (r) => r.created_at, cell: (r) => dateTime(r.created_at) },
          { id: "version", header: "Version", value: (r) => r.version, cell: (r) => r.version || "-" },
          { id: "guid", header: "Session", value: (r) => r.guid, cell: (r) => <Code title={r.guid}>{short(r.guid)}</Code> },
          { id: "log_lines", header: "Log lines", align: "right", value: (r) => Number(r.log_lines), cell: (r) => num(r.log_lines) },
        ]} empty={<EmptyState title="No installer has connected yet">Sessions appear when the Redbox store installer talks to the Update Service.</EmptyState>} />
      </Section>
      <Drawer opened={!!open} onClose={() => setOpen(null)} position="right" size="xl" title={<Text fw={800}>Installer session {short(open?.guid)}</Text>}>
        {open && (
          <Stack>
            <Facts cols={3} items={[["Started", dateTime(open.created_at)], ["Version", open.version], ["Hash", <Code>{short(open.hash)}</Code>]]} />
            {!open.log?.length ? <EmptyState title="The log is empty" /> : (
              <ScrollArea.Autosize mah="calc(100vh - 220px)" type="auto">
                <Code block style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12.5 }}>
                  {open.log.map((l, i) => `${String(i + 1).padStart(4)}  ${logLine(l)}`).join("\n")}
                </Code>
              </ScrollArea.Autosize>
            )}
          </Stack>
        )}
      </Drawer>
    </Stack>
  );
}

export function ProvisioningPage() {
  const { tab = "kiosks" } = useParams();
  const can = useCan();
  const q = useProvisioning();
  const d = q.data;
  return (
    <>
      <PageHeader title="Provisioning" description="Setting up new kiosks and pointing existing ones at this backend"
        actions={<>
          <Button component="a" href="/api/provisioning/ca.crt" variant="default" leftSection={<IconCertificate size={16} />}>Backend CA</Button>
          {can() && <ReprovisionButton />}
        </>} />
      <RouteTabs base="/provisioning" value={tab} tabs={[
        { value: "kiosks", label: "New kiosks", count: d ? d.installer_kiosks.length : undefined },
        { value: "certificates", label: "IoT certificates", count: d ? d.certificates.filter((c: Cert) => !c.revoked_at).length : undefined },
        { value: "installer", label: "Installer sessions", count: d ? d.installer_sessions.length : undefined },
      ]} />
      {q.isError ? <Section><ErrorState error={q.error} onRetry={() => q.refetch()} /></Section>
        : tab === "certificates" ? <CertificatesTab d={d} />
        : tab === "installer" ? <InstallerTab d={d} />
        : <KiosksTab d={d} />}
    </>
  );
}
