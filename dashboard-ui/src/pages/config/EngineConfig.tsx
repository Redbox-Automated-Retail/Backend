import { useMemo, useState } from "react";
import { Badge, Button, Drawer, Group, Skeleton, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconSearch } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { dateTime, num } from "@/lib/format";
import { confirm, notifyOk } from "@/lib/notify";
import { EmptyState, ErrorState, LocalTable } from "@/components/DataTable";
import { Facts, Section, useUrlParam } from "@/components/ui";

type ConfigFile = { oid: number; name: string; path: string; generation: number; settings: number; updated_at: string; stores_current: number };
type Setting = { CategoryName: string; SettingName: string; DefaultTypeName?: string; DefaultTypeValue?: string; Value: string | null; [k: string]: unknown };
type Detail = ConfigFile & { data: Setting[]; stores: { number: string; staged_gen: number | null; active_gen: number | null; reported_at: string }[] };

export function EngineConfig() {
  const q = useQuery({ queryKey: ["configfiles"], queryFn: () => api<ConfigFile[]>("/configfiles") });
  const [oid, setOid] = useUrlParam("oid");
  return (
    <Stack gap="md">
      <Text size="sm" c="dark.1">
        Delivered by updatemgrd's server poll (every 17 minutes) into Kiosk Engine\data and the Kiosk Client config folder. Saving creates a new generation.
      </Text>
      <Section title="Config files" p={0}>
        <LocalTable rows={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(f) => f.oid} onRowClick={(f) => setOid(String(f.oid))}
          empty={<EmptyState title="No config files imported">Import them with rb.ps1 import-store ... --config-files.</EmptyState>}
          columns={[
            { id: "oid", header: "OID", cell: (f) => f.oid, value: (f) => f.oid, align: "right", width: 80 },
            { id: "name", header: "Name", cell: (f) => <Text span size="sm" fw={700}>{f.name}</Text>, value: (f) => f.name },
            { id: "path", header: "Path", cell: (f) => f.path, value: (f) => f.path, width: 320 },
            { id: "generation", header: "Generation", cell: (f) => f.generation, value: (f) => f.generation, align: "right" },
            { id: "settings", header: "Settings", cell: (f) => num(f.settings), value: (f) => f.settings, align: "right" },
            { id: "stores_current", header: "Stores on it", cell: (f) => num(f.stores_current), value: (f) => f.stores_current, align: "right" },
            { id: "updated_at", header: "Updated", cell: (f) => dateTime(f.updated_at), value: (f) => f.updated_at },
          ]} />
      </Section>
      <Drawer opened={!!oid} onClose={() => setOid(null)} position="right" size="xl"
        title={<Text fw={800} fz="lg">{q.data?.find((f) => String(f.oid) === oid)?.name || "Config file"}</Text>}>
        {oid && <FileEditor key={oid} oid={oid} onClose={() => setOid(null)} />}
      </Drawer>
    </Stack>
  );
}

function FileEditor({ oid, onClose }: { oid: string; onClose: () => void }) {
  const can = useCan();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["configfiles", oid], queryFn: () => api<Detail>(`/configfiles/${oid}`) });
  const [filter, setFilter] = useState("");
  const [changes, setChanges] = useState<Record<number, string>>({});
  const changed = Object.keys(changes).length;
  const rows = useMemo(() => (q.data?.data || []).map((s, i) => ({ s, i }))
    .filter(({ s }) => !filter || `${s.CategoryName} ${s.SettingName} ${s.Value ?? ""}`.toLowerCase().includes(filter.toLowerCase())), [q.data, filter]);
  const save = useMutation({
    mutationFn: () => api<{ generation: number }>(`/configfiles/${oid}`, { method: "PUT", body: { changes: Object.entries(changes).map(([index, value]) => ({ index: Number(index), value })) } }),
    onSuccess: (x) => { notifyOk(`Generation ${x.generation} published`); setChanges({}); qc.invalidateQueries({ queryKey: ["configfiles"] }); onClose(); },
  });
  const set = (i: number, value: string) => setChanges((c) => {
    const n = { ...c };
    if ((q.data!.data[i].Value ?? "") === value) delete n[i]; else n[i] = value;
    return n;
  });
  const close = async () => {
    if (changed && !(await confirm({ title: "Discard changes?", body: `${changed} changed setting(s) are not saved.`, confirmLabel: "Discard", danger: true }))) return;
    onClose();
  };

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return <Stack gap="md"><Skeleton h={60} /><Skeleton h={400} /></Stack>;
  const d = q.data;
  return (
    <Stack gap="md">
      <Facts cols={4} items={[["Generation", d.generation], ["Settings", num(d.data.length)], ["Path", d.path], ["Updated", dateTime(d.updated_at)]]} />
      <Group gap="sm">
        <TextInput style={{ flex: 1 }} placeholder="Filter settings" leftSection={<IconSearch size={16} />} value={filter} onChange={(e) => setFilter(e.currentTarget.value)} aria-label="Filter settings" />
        {changed > 0 && <Badge color="yellow" variant="light" size="lg">{changed} changed</Badge>}
      </Group>
      <LocalTable rows={rows} rowKey={(r) => r.i} maxHeight="calc(100vh - 330px)"
        empty={<EmptyState title="No setting matches">Clear the filter.</EmptyState>}
        columns={[
          { id: "cat", header: "Category", cell: (r) => r.s.CategoryName, value: (r) => r.s.CategoryName },
          { id: "name", header: "Setting", cell: (r) => <Text span size="sm" fw={700}>{r.s.SettingName}</Text>, value: (r) => r.s.SettingName },
          { id: "scope", header: "Scope", cell: (r) => `${r.s.DefaultTypeName ?? ""} ${r.s.DefaultTypeValue ?? ""}`.trim(), value: (r) => `${r.s.DefaultTypeName} ${r.s.DefaultTypeValue}` },
          { id: "value", header: "Value", width: 360, cell: (r) => (
            <TextInput size="xs" readOnly={!can()} aria-label={`${r.s.SettingName} value`} value={r.i in changes ? changes[r.i] : r.s.Value ?? ""}
              onChange={(e) => set(r.i, e.currentTarget.value)}
              styles={r.i in changes ? { input: { borderColor: "var(--mantine-color-yellow-5)" } } : undefined} />) },
        ]} />
      {d.stores.length > 0 && (
        <Section title={`Stores (${num(d.stores.length)})`} p={0}>
          <LocalTable rows={d.stores} rowKey={(s) => s.number} maxHeight={260}
            columns={[
              { id: "number", header: "Store", cell: (s) => s.number, value: (s) => Number(s.number) || s.number },
              { id: "active_gen", header: "Active generation", cell: (s) => s.active_gen ?? "-", value: (s) => s.active_gen },
              { id: "staged_gen", header: "Staged generation", cell: (s) => s.staged_gen ?? "-", value: (s) => s.staged_gen },
              { id: "reported_at", header: "Reported", cell: (s) => dateTime(s.reported_at), value: (s) => s.reported_at },
            ]} />
        </Section>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={close}>Close</Button>
        {can() && <Button disabled={!changed} loading={save.isPending} onClick={() => save.mutate()}>Save new generation</Button>}
      </Group>
    </Stack>
  );
}
