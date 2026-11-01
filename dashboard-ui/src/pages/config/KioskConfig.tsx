import { useEffect, useMemo, useState } from "react";
import { ActionIcon, Autocomplete, Button, Checkbox, Drawer, Group, NumberInput, SegmentedControl, Select, Skeleton, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconPlus, IconSend, IconTrash } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { dateTime, num } from "@/lib/format";
import { confirm, notifyOk } from "@/lib/notify";
import { EmptyState, LocalTable } from "@/components/DataTable";
import { Section, StatCards, UrlSearch, useUrlParam } from "@/components/ui";

type Value = {
  value_id: number; category: string; name: string; data_type: string; value: string | null; encryption_type: number | null;
  effective_at: string | null; expires_at: string | null; segmentation_name: string; segment_key: string | null; rank: number;
};
type KState = { kiosk_id: number; reported_version: number | null; activated_version: number | null; activated_at: string | null; updated_at: string };
type KConfig = { version: number; values: Value[]; kiosks: KState[] };
type Pushed = { delivered?: number } | null;

const TYPES = ["string", "bool", "int", "long", "decimal"] as const;
type Scope = "all" | "market" | "kiosk";
type FormV = { category: string; setting: string; value: string; data_type: (typeof TYPES)[number]; scope: Scope; target: string; push: boolean };

const scopeOf = (v: Value): Scope => {
  const s = (v.segmentation_name || "").toLowerCase();
  return s === "all" ? "all" : s.startsWith("market") ? "market" : "kiosk";
};
const scopeText = (v: Value) => (v.segmentation_name === "All" ? "all kiosks" : `${v.segmentation_name} ${v.segment_key ?? ""}`);

const schema = z.object({
  category: z.string().trim().min(1, "Give a category"),
  setting: z.string().trim().min(1, "Give a setting name"),
  value: z.string(),
  data_type: z.enum(TYPES),
  scope: z.enum(["all", "market", "kiosk"]),
  target: z.string(),
  push: z.boolean(),
}).superRefine((v, ctx) => {
  if (v.scope === "kiosk" && !/^\d+$/.test(v.target.trim())) ctx.addIssue({ code: "custom", path: ["target"], message: "Give a kiosk id" });
  if (v.scope === "market" && !v.target.trim()) ctx.addIssue({ code: "custom", path: ["target"], message: "Give a market" });
  if ((v.data_type === "int" || v.data_type === "long") && v.value !== "" && !/^-?\d+$/.test(v.value)) ctx.addIssue({ code: "custom", path: ["value"], message: "A whole number" });
  if (v.data_type === "decimal" && v.value !== "" && Number.isNaN(Number(v.value))) ctx.addIssue({ code: "custom", path: ["value"], message: "A number" });
  if (v.data_type === "bool" && !/^(true|false)$/i.test(v.value)) ctx.addIssue({ code: "custom", path: ["value"], message: "Choose true or false" });
});

export function KioskConfig() {
  const can = useCan();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["kconfig"], queryFn: () => api<KConfig>("/kconfig") });
  const [search] = useUrlParam("q");
  const [scopeF, setScopeF] = useUrlParam("scope");
  const [edit, setEdit] = useState<Partial<FormV> | null>(null);
  const [pushKiosk, setPushKiosk] = useState("");
  const del = useMutation({
    mutationFn: (id: number) => api<{ version: number }>("/kconfig/delete", { body: { value_id: id } }),
    onSuccess: (x) => { notifyOk(`Deleted (configuration version ${x.version})`); qc.invalidateQueries({ queryKey: ["kconfig"] }); },
  });
  const push = useMutation({
    mutationFn: (b: { scope: "all" | "kiosk"; target?: string }) => api<Pushed>("/kconfig/push", { body: b }),
    onSuccess: (x) => notifyOk(`Pushed (${x?.delivered ?? 0} deliveries)`),
  });
  const values = useMemo(() => (q.data?.values || []).filter((v) =>
    (!scopeF || scopeOf(v) === scopeF) &&
    (!search || `${v.category} ${v.name} ${v.value ?? ""}`.toLowerCase().includes(search.toLowerCase()))), [q.data, search, scopeF]);
  const kiosks = q.data?.kiosks || [];
  const onVersion = kiosks.filter((k) => k.activated_version === q.data?.version).length;

  const remove = async (v: Value) => {
    if (await confirm({
      title: "Delete this value?",
      body: `${v.category} / ${v.name} for ${scopeText(v)} is removed. Kiosks fall back to the next value (market, then default) on their next configuration update.`,
      confirmLabel: "Delete value", danger: true,
    })) del.mutate(v.value_id);
  };

  return (
    <Stack gap="md">
      <Text size="sm" c="dark.1">
        Written to configuration.json on each kiosk by the Update Client (read by the Kiosk Client). Kiosk values beat market values beat defaults.
      </Text>
      <StatCards loading={q.isPending} cols={3} stats={[
        { label: "Configuration version", value: q.data ? num(q.data.version) : "-" },
        { label: "Values", value: num(q.data?.values.length) },
        { label: "Kiosks on this version", value: `${num(onVersion)} / ${num(kiosks.length)}`, hint: "activated the current version" },
      ]} />
      <Section title="Values" p={0} actions={can() && <>
        <Group gap={6} wrap="nowrap">
          <TextInput size="xs" w={110} placeholder="kiosk id" value={pushKiosk} onChange={(e) => setPushKiosk(e.currentTarget.value.replace(/\D/g, ""))} aria-label="Kiosk id to push to" />
          <Button size="xs" variant="default" disabled={!pushKiosk} loading={push.isPending && push.variables?.scope === "kiosk"}
            onClick={() => push.mutate({ scope: "kiosk", target: pushKiosk })}>Push to kiosk</Button>
        </Group>
        <Button size="xs" variant="default" leftSection={<IconSend size={14} />} loading={push.isPending && push.variables?.scope === "all"}
          onClick={async () => { if (await confirm({ title: "Push to all kiosks?", body: "Every connected kiosk is told to fetch its configuration now.", confirmLabel: "Push" })) push.mutate({ scope: "all" }); }}>
          Push to all kiosks
        </Button>
        <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setEdit({})}>Set a value</Button>
      </>}>
        <Group gap="sm" px="md" py="sm" wrap="wrap">
          <UrlSearch placeholder="Search category, setting or value" />
          <Select w={170} clearable placeholder="Any scope" value={scopeF} onChange={setScopeF} aria-label="Filter by scope"
            data={[{ value: "all", label: "All kiosks" }, { value: "market", label: "Market" }, { value: "kiosk", label: "Kiosk" }]} />
        </Group>
        <LocalTable rows={values} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(v) => v.value_id} maxHeight={520}
          onRowClick={can() ? (v) => setEdit({ category: v.category, setting: v.name, value: v.value ?? "", data_type: (TYPES as readonly string[]).includes(v.data_type) ? v.data_type as FormV["data_type"] : "string", scope: scopeOf(v), target: v.segment_key ?? "" }) : undefined}
          empty={<EmptyState title={q.data?.values.length ? "No value matches" : "No values yet"}>{q.data?.values.length ? "Clear the search or the scope filter." : "Set a value to send it to the kiosks."}</EmptyState>}
          columns={[
            { id: "category", header: "Category", cell: (v) => v.category, value: (v) => v.category },
            { id: "name", header: "Setting", cell: (v) => <Text span size="sm" fw={700}>{v.name}</Text>, value: (v) => v.name },
            { id: "value", header: "Value", cell: (v) => v.value, value: (v) => v.value, width: 320 },
            { id: "data_type", header: "Type", cell: (v) => v.data_type, value: (v) => v.data_type },
            { id: "scope", header: "Scope", cell: scopeText, value: (v) => `${v.rank} ${v.segment_key}` },
            ...(can() ? [{ id: "act", header: "", cell: (v: Value) => (
              <ActionIcon variant="subtle" color="red" aria-label={`Delete ${v.name}`} onClick={() => remove(v)} loading={del.isPending && del.variables === v.value_id}>
                <IconTrash size={16} />
              </ActionIcon>) }] : []),
          ]} />
      </Section>
      <Section title="Kiosks" p={0}>
        <LocalTable rows={kiosks} loading={q.isPending} rowKey={(k) => k.kiosk_id} maxHeight={420}
          empty={<EmptyState title="No kiosk has reported">Kiosks report the configuration version they have after the Update Client fetches it.</EmptyState>}
          columns={[
            { id: "kiosk_id", header: "Kiosk", cell: (k) => k.kiosk_id, value: (k) => k.kiosk_id },
            { id: "reported_version", header: "Reported", cell: (k) => k.reported_version ?? "-", value: (k) => k.reported_version },
            { id: "activated_version", header: "Activated", cell: (k) => k.activated_version ?? "-", value: (k) => k.activated_version },
            { id: "activated_at", header: "Activated at", cell: (k) => dateTime(k.activated_at), value: (k) => k.activated_at },
            { id: "updated_at", header: "Last report", cell: (k) => dateTime(k.updated_at), value: (k) => k.updated_at },
          ]} />
      </Section>
      <SetValueDrawer preset={edit} values={q.data?.values || []} onClose={() => setEdit(null)} />
    </Stack>
  );
}

function SetValueDrawer({ preset, values, onClose }: { preset: Partial<FormV> | null; values: Value[]; onClose: () => void }) {
  return (
    <Drawer opened={!!preset} onClose={onClose} position="right" size="lg" title={<Text fw={800} fz="lg">{preset?.setting ? `Set ${preset.setting}` : "Set a value"}</Text>}>
      {preset ? <SetValueForm preset={preset} values={values} onClose={onClose} /> : <Skeleton h={200} />}
    </Drawer>
  );
}

function SetValueForm({ preset, values, onClose }: { preset: Partial<FormV>; values: Value[]; onClose: () => void }) {
  const qc = useQueryClient();
  const form = useForm<FormV>({
    initialValues: { category: "", setting: "", value: "", data_type: "string", scope: "all", target: "", push: true, ...preset },
    validate: zodResolver(schema),
  });
  const v = form.values;
  const cats = useMemo(() => [...new Set(values.map((x) => x.category))].sort(), [values]);
  const names = useMemo(() => [...new Set(values.filter((x) => x.category === v.category).map((x) => x.name))].sort(), [values, v.category]);
  // a known setting brings its type along
  useEffect(() => {
    if (preset.setting) return;
    const known = values.find((x) => x.category === v.category && x.name === v.setting);
    if (known && (TYPES as readonly string[]).includes(known.data_type)) form.setFieldValue("data_type", known.data_type as FormV["data_type"]);
  }, [v.category, v.setting]); // eslint-disable-line react-hooks/exhaustive-deps
  const m = useMutation({
    mutationFn: (x: FormV) => api<{ version: number; pushed: Pushed }>("/kconfig", {
      body: { category: x.category.trim(), setting: x.setting.trim(), value: x.value, data_type: x.data_type, scope: x.scope, target: x.scope === "all" ? null : x.target.trim(), push: x.push },
    }),
    onSuccess: (x) => {
      notifyOk(`Saved (version ${x.version})` + (x.pushed ? `, pushed to ${x.pushed.delivered ?? 0}` : ""));
      qc.invalidateQueries({ queryKey: ["kconfig"] });
      onClose();
    },
  });
  const boolCase = /^[TF]/.test(v.value) ? ["True", "False"] : ["true", "false"];
  const valueInput = v.data_type === "bool" ? (
    <Stack gap={4}>
      <Text size="sm" fw={600}>Value</Text>
      <SegmentedControl data={boolCase} value={/^true$/i.test(v.value) ? boolCase[0] : /^false$/i.test(v.value) ? boolCase[1] : ""}
        onChange={(x) => form.setFieldValue("value", x)} />
      {form.errors.value && <Text size="xs" c="red.4">{form.errors.value}</Text>}
    </Stack>
  ) : v.data_type === "int" || v.data_type === "long" || v.data_type === "decimal" ? (
    <NumberInput label="Value" allowDecimal={v.data_type === "decimal"} value={v.value} error={form.errors.value}
      onChange={(x) => form.setFieldValue("value", String(x))} />
  ) : (
    <Textarea label="Value" autosize minRows={1} maxRows={8} {...form.getInputProps("value")} />
  );
  return (
    <form onSubmit={form.onSubmit((x) => m.mutate(x))}>
      <Stack gap="md">
        <Autocomplete label="Category" withAsterisk data={cats} {...form.getInputProps("category")} />
        <Autocomplete label="Setting" withAsterisk data={names} {...form.getInputProps("setting")} />
        <Select label="Type" allowDeselect={false} data={[...TYPES]} {...form.getInputProps("data_type")}
          onChange={(x) => { form.setFieldValue("data_type", (x || "string") as FormV["data_type"]); if (x === "bool" && !/^(true|false)$/i.test(v.value)) form.setFieldValue("value", "true"); }} />
        {valueInput}
        <Stack gap={4}>
          <Text size="sm" fw={600}>Scope</Text>
          <SegmentedControl data={[{ value: "all", label: "All kiosks" }, { value: "market", label: "A market" }, { value: "kiosk", label: "One kiosk" }]} {...form.getInputProps("scope")} />
        </Stack>
        {v.scope !== "all" && <TextInput label={v.scope === "market" ? "Market" : "Kiosk id"} withAsterisk {...form.getInputProps("target")} />}
        <Checkbox label="Push to the kiosks now" {...form.getInputProps("push", { type: "checkbox" })} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={!form.isDirty()} loading={m.isPending}>Set value</Button>
        </Group>
      </Stack>
    </form>
  );
}
