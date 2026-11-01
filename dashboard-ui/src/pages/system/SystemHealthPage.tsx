import { useQuery } from "@tanstack/react-query";
import { Button, Group, Progress, SimpleGrid, Skeleton, Stack, Text } from "@mantine/core";
import { IconDownload, IconRefresh } from "@tabler/icons-react";
import { LocalTable, ErrorState, EmptyState } from "@/components/DataTable";
import { Facts, PageHeader, Section, StatCards, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes, date, duration, num } from "@/lib/format";

type Service = { name: string; role: string; ok: boolean; ms?: number | null; status?: number | null; error?: string; url?: string; detail?: any };
type Cert = { file: string; subject?: string; issuer?: string; not_after?: string; days_left?: number; names?: string[]; error?: string };
type Sys = {
  services: Service[];
  certificates: Cert[];
  settings: Record<string, string | number | boolean>;
  tables: { table: string; rows: number; bytes: number }[];
  disk: { path: string; total: number; used: number; free: number } | null;
  runtime: { python: string; platform: string; pid: number; uptime_s: number };
};

function details(s: Service): string {
  const v = s.detail;
  if (s.name === "rb-db" && v) return `${String(v.version).split(" ").slice(0, 2).join(" ")} · ${bytes(v.size)} · ${v.connections} connections · up ${duration(v.uptime_s)}`;
  if (s.name === "rb-iot" && v && typeof v === "object") return `${num(v.clients)} MQTT clients`;
  if (s.name === "rb-remoting" && v && typeof v === "object") return `${num(v.kiosks ?? 0)} kiosks on the broker`;
  if (s.name === "rb-admin" && v) return `up ${duration(v.uptime_s)}`;
  return s.url || "";
}

const certKind = (d: number) => (d < 14 ? "bad" : d < 60 ? "warn" : "ok");

export function SystemHealthPage() {
  const q = useQuery({ queryKey: ["system"], queryFn: () => api<Sys>("/system") });
  const s = q.data;
  const up = s ? s.services.filter((x) => x.ok).length : 0;
  const dbSize = s?.services.find((x) => x.name === "rb-db")?.detail?.size;
  const certDays = s?.certificates.filter((c) => c.days_left != null).map((c) => c.days_left!) || [];
  const minDays = certDays.length ? Math.min(...certDays) : null;

  return (
    <>
      <PageHeader title="System health"
        description={s ? `${up} of ${s.services.length} services healthy · dashboard up ${duration(s.runtime.uptime_s)}` : "Services, certificates, storage and settings of this backend"}
        actions={<Button variant="default" leftSection={<IconRefresh size={16} />} loading={q.isFetching} onClick={() => q.refetch()}>Refresh</Button>} />
      {q.isError ? <Section><ErrorState error={q.error} onRetry={() => q.refetch()} /></Section> : (
        <>
          <StatCards loading={!s} stats={[
            { label: "Services healthy", value: s ? `${up} / ${s.services.length}` : "", color: !s ? undefined : up === s.services.length ? "teal.4" : "red.4",
              hint: s && up < s.services.length ? `Down: ${s.services.filter((x) => !x.ok).map((x) => x.name).join(", ")}` : "every service answers" },
            { label: "Database size", value: dbSize != null ? bytes(dbSize) : "-", hint: s?.tables[0] ? `largest: ${s.tables[0].table}` : undefined },
            { label: "Disk free", value: s?.disk ? bytes(s.disk.free) : "-", hint: s?.disk ? `of ${bytes(s.disk.total)} at ${s.disk.path}` : "unknown",
              color: s?.disk && s.disk.free / s.disk.total < 0.1 ? "red.4" : undefined },
            { label: "Certificate days left", value: minDays ?? "-", color: minDays == null ? undefined : minDays < 14 ? "red.4" : minDays < 60 ? "yellow.4" : "teal.4",
              hint: minDays != null && minDays < 60 ? "renew soon" : "the soonest expiry" },
          ]} />
          <Stack gap="lg">
            <Section title="Services" p={0}>
              <LocalTable<Service> rows={s?.services} loading={!s} rowKey={(r) => r.name} columns={[
                { id: "name", header: "Service", value: (r) => r.name, cell: (r) => <div><Text fw={700} size="sm">{r.name}</Text><Text size="xs" c="dark.1" style={{ whiteSpace: "normal" }}>{r.role}</Text></div>, width: 380 },
                { id: "ok", header: "Status", value: (r) => (r.ok ? 1 : 0), cell: (r) => r.ok ? <Status kind="ok">Healthy</Status> : (
                  <div><Status kind="bad">Down</Status><Text size="xs" c="red.3" maw={360} style={{ whiteSpace: "normal" }}>{r.error || `HTTP ${r.status}`}</Text></div>
                ) },
                { id: "ms", header: "Answer time", align: "right", value: (r) => r.ms ?? null, cell: (r) => (r.ms != null ? `${r.ms} ms` : "") },
                { id: "detail", header: "Details", cell: (r) => <Text size="sm" style={{ whiteSpace: "normal" }}>{details(r)}</Text> },
              ]} />
            </Section>
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
              <Section title="Certificates" actions={<Button component="a" href="/api/provisioning/ca.crt" size="xs" variant="default" leftSection={<IconDownload size={14} />}>Download the CA</Button>}>
                {!s ? <Skeleton h={120} /> : !s.certificates.length ? <EmptyState title="No certificates found" /> : (
                  <Stack gap="lg">
                    {s.certificates.map((c) => (
                      <div key={c.file}>
                        <Group justify="space-between" mb={6}>
                          <Text fw={700}>{c.file}</Text>
                          {c.days_left != null && <Status kind={certKind(c.days_left)}>{c.days_left} days left</Status>}
                        </Group>
                        {c.error ? <Text c="red.3" size="sm">{c.error}</Text> : (
                          <Facts cols={2} items={[
                            ["Subject", <Text size="sm" fw={600} style={{ wordBreak: "break-all" }}>{c.subject}</Text>],
                            ["Expires", date(c.not_after)],
                            ["Issuer", <Text size="sm" fw={600} style={{ wordBreak: "break-all" }}>{c.issuer}</Text>],
                            ["Names", <Text size="sm" fw={600} style={{ wordBreak: "break-all" }}>{c.names?.length ? c.names.join(", ") : "-"}</Text>],
                          ]} />
                        )}
                      </div>
                    ))}
                  </Stack>
                )}
              </Section>
              <Section title="Settings" actions={<Text size="sm" c="dark.1">from .env, read-only</Text>} p={0}>
                <LocalTable rows={s ? Object.entries(s.settings) : undefined} loading={!s} rowKey={(r) => r[0]} maxHeight={460} columns={[
                  { id: "k", header: "Setting", value: (r) => r[0], cell: (r) => <Text size="sm" fw={600}>{r[0]}</Text> },
                  { id: "v", header: "Value", value: (r) => String(r[1]), cell: (r) => <Text size="sm" style={{ wordBreak: "break-all", whiteSpace: "normal" }}>{typeof r[1] === "boolean" ? (r[1] ? "yes" : "no") : String(r[1])}</Text> },
                ]} />
              </Section>
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
              <Section title="Largest tables" p={0}>
                <LocalTable rows={s?.tables} loading={!s} rowKey={(r) => r.table} initialSort={{ sort: "bytes", dir: "desc" }} columns={[
                  { id: "table", header: "Table", value: (r) => r.table, cell: (r) => r.table },
                  { id: "rows", header: "Rows", align: "right", value: (r) => Number(r.rows), cell: (r) => num(r.rows) },
                  { id: "bytes", header: "Size", align: "right", value: (r) => Number(r.bytes), cell: (r) => bytes(r.bytes) },
                ]} />
              </Section>
              <Section title="Storage and runtime">
                {!s ? <Skeleton h={120} /> : (
                  <Stack>
                    {s.disk ? (
                      <div>
                        <Group justify="space-between"><Text fw={700}>{s.disk.path}</Text><Text size="sm">{bytes(s.disk.used)} of {bytes(s.disk.total)} used</Text></Group>
                        <Progress mt={8} size="lg" value={(s.disk.used / s.disk.total) * 100}
                          color={s.disk.free / s.disk.total < 0.1 ? "red" : s.disk.free / s.disk.total < 0.25 ? "yellow" : "teal"} aria-label="Disk use" />
                        <Text size="sm" mt={6} c="dark.1">{bytes(s.disk.free)} free</Text>
                      </div>
                    ) : <Text>Disk use unknown.</Text>}
                    <Facts cols={2} items={[["Python", s.runtime.python], ["Process id", s.runtime.pid], ["Dashboard uptime", duration(s.runtime.uptime_s)],
                      ["Platform", <Text size="sm" fw={600} style={{ wordBreak: "break-all" }}>{s.runtime.platform}</Text>]]} />
                  </Stack>
                )}
              </Section>
            </SimpleGrid>
          </Stack>
        </>
      )}
    </>
  );
}
