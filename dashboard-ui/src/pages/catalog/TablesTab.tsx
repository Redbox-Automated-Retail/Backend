// The other profile.data tables: rows are Lua table literals, as the kiosk engine reads them.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Code, Drawer, Group, Select, Skeleton, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { api, qs, type Page } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { num } from "@/lib/format";
import { EmptyState, LocalTable, ServerTable, type Column } from "@/components/DataTable";
import { Toolbar, UrlSearch, useUrlParam } from "@/components/ui";
import { useCatalogStatus } from "./shared";

type Row = { key: string; lua: string };
const LOCAL_MAX = 1000;
const enc = encodeURIComponent;

export function TablesTab() {
  const can = useCan()("change");
  const s = useCatalogStatus();
  const [tbl, setTbl] = useUrlParam("table");
  const [q] = useUrlParam("q");
  const [open, setOpen] = useState<Row | "new" | null>(null);
  const names = (s.data?.tables || []).filter((t) => t.name !== "Cache");
  const table = tbl || names[0]?.name || "";
  // small tables (most of them) come whole and sort in the browser; big ones page from the server
  const first = useQuery({
    queryKey: ["catalog", "rows", table, q || ""],
    queryFn: () => api<Page<Row>>(`/catalog/rows/${enc(table)}` + qs({ q, limit: LOCAL_MAX })),
    enabled: !!table,
  });
  const big = !!first.data && first.data.total > first.data.rows.length;
  const columns: (Column<Row> & { value?: (r: Row) => string })[] = [
    { id: "key", header: "Key", cell: (r) => r.key, value: (r) => r.key, width: 180 },
    { id: "lua", header: "Value", cell: (r) => <Code fz={12} bg="transparent" c="inherit">{r.lua.slice(0, 300)}</Code> },
  ];
  if (s.isPending) return <Stack><Skeleton h={36} w={400} /><Skeleton h={360} /></Stack>;
  if (!names.length) return <EmptyState title="No catalog tables">Import a catalog under System administration first.</EmptyState>;
  return (
    <>
      <Toolbar>
        <Select w={240} aria-label="Table" searchable allowDeselect={false} value={table} onChange={(v) => setTbl(v)}
          data={names.map((t) => ({ value: t.name, label: `${t.name} (${num(t.rows)})` }))} />
        <UrlSearch placeholder="Key or text in the value" />
        {can && <Button leftSection={<IconPlus size={16} />} onClick={() => setOpen("new")}>New row</Button>}
      </Toolbar>
      <Text size="sm" fw={600} mb="xs">
        Rows are Lua table literals, as the engine reads them.{first.data ? ` ${num(first.data.total)} rows${big ? " (shown in table order; too many to sort here)" : ""}.` : ""}
      </Text>
      {big ? (
        <ServerTable<Row> queryKey={["catalog", "rows-paged", table, q || ""]} columns={columns} rowKey={(r) => r.key} onRowClick={setOpen}
          fetchPage={(p) => api<Page<Row>>(`/catalog/rows/${enc(table)}` + qs({ q, limit: p.limit, offset: p.offset }))} />
      ) : (
        <LocalTable rows={first.data?.rows} loading={first.isPending} error={first.error} onRetry={() => first.refetch()} columns={columns}
          rowKey={(r) => r.key} onRowClick={setOpen} maxHeight="calc(100vh - 330px)"
          empty={<EmptyState title={q ? "No row matches" : "This table is empty"}>{can ? "Add one with New row." : undefined}</EmptyState>} />
      )}
      <RowDrawer table={table} row={open} onClose={() => setOpen(null)} />
    </>
  );
}

function RowDrawer({ table, row, onClose }: { table: string; row: Row | "new" | null; onClose: () => void }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const isNew = row === "new";
  const orig = row && row !== "new" ? row.lua : "{  }";
  const [key, setKey] = useState("");
  const [text, setText] = useState(orig);
  useEffect(() => { setKey(""); setText(orig); }, [row]); // eslint-disable-line react-hooks/exhaustive-deps
  const k = isNew ? key.trim() : (row as Row | null)?.key || "";
  const save = useMutation({
    mutationFn: () => api(`/catalog/rows/${enc(table)}/${enc(k)}`, { method: "PUT", body: { lua: text } }),
    onSuccess: () => { notifyOk(`${table} ${k} saved`); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
  });
  const del = useMutation({
    mutationFn: () => api(`/catalog/rows/${enc(table)}/${enc(k)}/delete`, { body: {} }),
    onSuccess: () => { notifyOk(`${table} ${k} deleted`); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
  });
  const ask = async () => {
    if (await confirm({ title: `Delete ${table} row ${k}?`, danger: true, confirmLabel: "Delete row",
      body: "The row leaves profile.data with the next publish. Rows in other tables that point at it are not changed." })) del.mutate();
  };
  return (
    <Drawer opened={!!row} onClose={onClose} position="right" size="lg" title={<Text fw={800} size="lg">{table} {isNew ? "(new row)" : k}</Text>}>
      <form onSubmit={(e) => { e.preventDefault(); if (k && text.trim()) save.mutate(); }}>
        <Stack gap="sm">
          {isNew && <TextInput label="Key" withAsterisk value={key} onChange={(e) => setKey(e.currentTarget.value)} error={key && !key.trim() ? "Give the row a key" : undefined} />}
          <Textarea label="Value (Lua table)" autosize minRows={12} maxRows={30} value={text} onChange={(e) => setText(e.currentTarget.value)} readOnly={!can}
            styles={{ input: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12.5 } }} />
          {can && (
            <Group justify="space-between" mt="md">
              {!isNew ? <Button color="red" variant="light" leftSection={<IconTrash size={16} />} loading={del.isPending} onClick={ask}>Delete row</Button> : <span />}
              <Button type="submit" loading={save.isPending} disabled={!k || !text.trim() || (!isNew && text === orig)}>Save</Button>
            </Group>
          )}
        </Stack>
      </form>
    </Drawer>
  );
}
