import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Drawer, Group, JsonInput, NumberInput, Select, Stack, Text, TextInput, Textarea, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { api, qs } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { dateTime, num } from "@/lib/format";
import { confirm, notifyOk } from "@/lib/notify";
import { DateField, DateTimeField } from "@/components/fields";
import { EmptyState, ErrorState, ServerTable, useUrlSort, type Column } from "@/components/DataTable";
import { Toolbar, UrlSearch, useUrlParam } from "@/components/ui";

type Col = { column_name: string; type: string; nullable: boolean; column_default: string | null; data_type: string };
type Meta = { title: string; key: string[]; total: number; rows: Row[]; columns: Col[] };
type Row = Record<string, unknown>;
const MASK = "********";

const isTime = (c: Col) => c.type.startsWith("timestamp");
const isDate = (c: Col) => c.type === "date";
const isJson = (c: Col) => c.type === "jsonb" || c.type === "json" || c.type.endsWith("[]");
const isNum = (c: Col) => ["integer", "bigint", "smallint", "numeric", "double precision", "real"].includes(c.type);

function show(v: unknown, c: Col) {
  if (v === null || v === undefined) return <Text span size="sm" c="dark.1">-</Text>;
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (isTime(c)) return dateTime(String(v));
  if (typeof v === "object") return <Text span size="xs" ff="monospace">{JSON.stringify(v).slice(0, 120)}</Text>;
  return String(v);
}

/** Generic editor for one of the dashboard's tables (GET/POST/PUT /api/tables/{name}): sortable, searchable, paged on
 *  the server; a drawer edits a row with an input that fits each column's type. */
export function TableEditor({ name, kioskFilter = true }: { name: string; kioskFilter?: boolean }) {
  const can = useCan();
  const [q] = useUrlParam("q");
  const [kiosk, setKiosk] = useUrlParam("kiosk");
  const [sort, setSort] = useUrlSort({ dir: "asc" }, "");
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const meta = useQuery({ queryKey: ["tables", name, "meta"], queryFn: () => api<Meta>(`/tables/${name}` + qs({ limit: 1 })), staleTime: 300_000 });
  const m = meta.data;
  const hasKiosk = !!m?.columns.some((c) => c.column_name === "kiosk_id");
  const columns: Column<Row>[] = useMemo(() => (m?.columns || []).map((c) => ({
    id: c.column_name, header: c.column_name.replace(/_/g, " "), sort: c.type === "bytea" ? undefined : c.column_name,
    align: isNum(c) ? "right" as const : undefined, width: isJson(c) ? 260 : undefined,
    cell: (r: Row) => m!.key.includes(c.column_name) ? <Text span fw={700} size="sm">{show(r[c.column_name], c)}</Text> : show(r[c.column_name], c),
  })), [m]);
  if (meta.isError) return <ErrorState error={meta.error} onRetry={() => meta.refetch()} />;
  const filters = { q, kiosk_id: hasKiosk ? kiosk : undefined };
  return (
    <>
      <Toolbar>
        <UrlSearch placeholder="Search text columns or the key" />
        {kioskFilter && hasKiosk && <NumberInput placeholder="Kiosk ID" w={150} hideControls value={kiosk ?? ""} onChange={(v) => setKiosk(v === "" ? null : String(v))} aria-label="Kiosk" />}
        {can() && m && <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing("new")}>New row</Button>}
      </Toolbar>
      {m && (
        <ServerTable<Row> queryKey={["tables", name, filters]} columns={columns} sort={sort} onSort={setSort}
          fetchPage={async (p) => { const d = await api<Meta>(`/tables/${name}` + qs({ ...filters, ...p })); return { rows: d.rows, total: d.total }; }}
          rowKey={(r) => m.key.map((k) => String(r[k])).join("|")} onRowClick={setEditing}
          summary={(t) => `${num(t)} rows in ${m.title}`}
          empty={<EmptyState title={q || kiosk ? "No row matches" : "This table is empty"}>{can() ? "Add a row with New row." : undefined}</EmptyState>} />
      )}
      {m && <RowDrawer name={name} meta={m} row={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function RowDrawer({ name, meta, row, onClose }: { name: string; meta: Meta; row: Row | "new" | null; onClose: () => void }) {
  const can = useCan();
  const qc = useQueryClient();
  const isNew = row === "new";
  const toForm = (r: Row | null): Record<string, unknown> => Object.fromEntries(meta.columns.map((c) => {
    const v = r ? r[c.column_name] : null;
    if (v === null || v === undefined) return [c.column_name, isJson(c) ? "" : null];
    return [c.column_name, isJson(c) ? JSON.stringify(v, null, 2) : typeof v === "boolean" ? String(v) : v];
  }));
  const form = useForm<Record<string, unknown>>({ initialValues: toForm(null) });
  useEffect(() => { const v = toForm(row && row !== "new" ? row : null); form.setValues(v); form.resetDirty(v); }, [row]); // eslint-disable-line react-hooks/exhaustive-deps
  const keyOf = () => Object.fromEntries(meta.key.map((k) => [k, (row as Row)[k]]));
  const changed = () => Object.fromEntries(meta.columns.filter((c) => c.type !== "bytea" && form.isDirty(c.column_name) && !(c.column_name in (isNew ? {} : keyOf())))
    .map((c) => [c.column_name, form.values[c.column_name] === "" ? null : form.values[c.column_name]]));
  const save = useMutation({
    mutationFn: () => isNew ? api(`/tables/${name}`, { body: changed() }) : api(`/tables/${name}`, { method: "PUT", body: { key: keyOf(), values: changed() } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["tables", name] }); notifyOk("Saved"); onClose(); },
  });
  const del = useMutation({
    mutationFn: () => api(`/tables/${name}/delete`, { body: keyOf() }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["tables", name] }); notifyOk("Deleted"); onClose(); },
  });
  const ro = !can();
  return (
    <Drawer opened={row !== null} onClose={onClose} position="right" size="lg" title={<Title order={3}>{isNew ? `New row: ${meta.title}` : meta.title}</Title>}>
      <form onSubmit={form.onSubmit(() => save.mutate())}>
        <Stack gap="sm">
          {meta.columns.map((c) => {
            const k = c.column_name;
            const locked = ro || c.type === "bytea" || (!isNew && meta.key.includes(k));
            const label = <Group gap={6}>{k}{meta.key.includes(k) && <Badge size="xs" variant="light">key</Badge>}{!c.nullable && !c.column_default && <Badge size="xs" variant="light" color="red">required</Badge>}</Group>;
            const desc = `${c.type}${c.column_default ? ` · default ${c.column_default}` : ""}`;
            const p = form.getInputProps(k);
            if (c.type === "bytea") return <TextInput key={k} label={label} description="binary data (not editable here)" value={`${p.value ?? 0} bytes`} disabled />;
            if (c.type === "boolean") return <Select key={k} label={label} description={desc} disabled={locked} data={[{ value: "true", label: "yes" }, { value: "false", label: "no" }]}
              clearable={c.nullable} {...p} value={p.value == null ? null : String(p.value)} />;
            if (isTime(c)) return <DateTimeField key={k} label={k} description={desc} value={p.value as string | null} onChange={(v) => form.setFieldValue(k, v)} />;
            if (isDate(c)) return <DateField key={k} label={label} description={desc} disabled={locked} value={p.value as string | null} onChange={(v) => form.setFieldValue(k, v)} />;
            if (isJson(c)) return <JsonInput key={k} label={label} description={desc} disabled={locked} autosize minRows={2} maxRows={12} formatOnBlur validationError="Not valid JSON" {...p} value={(p.value as string) ?? ""} />;
            if (isNum(c)) return <NumberInput key={k} label={label} description={desc} disabled={locked} decimalScale={c.type === "numeric" || c.type.includes("double") ? 4 : 0} {...p} value={p.value as number | string ?? ""} />;
            if (p.value === MASK) return <TextInput key={k} label={label} description="secret: shown masked; type a new value to change it" disabled={locked} {...p} />;
            return String(p.value ?? "").length > 80
              ? <Textarea key={k} label={label} description={desc} disabled={locked} autosize minRows={2} {...p} value={(p.value as string) ?? ""} />
              : <TextInput key={k} label={label} description={desc} disabled={locked} {...p} value={(p.value as string) ?? ""} />;
          })}
          {!ro && (
            <Group justify="space-between" mt="md" pos="sticky" bottom={0} py="sm" bg="var(--mantine-color-body)">
              {!isNew ? <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} loading={del.isPending}
                onClick={async () => { if (await confirm({ title: "Delete this row?", danger: true, confirmLabel: "Delete", body: `It is removed from ${meta.title} at once.` })) del.mutate(); }}>Delete</Button> : <span />}
              <Group><Button variant="default" onClick={onClose}>Cancel</Button>
                <Button type="submit" loading={save.isPending} disabled={!form.isDirty()}>{isNew ? "Add row" : "Save changes"}</Button></Group>
            </Group>
          )}
        </Stack>
      </form>
    </Drawer>
  );
}
