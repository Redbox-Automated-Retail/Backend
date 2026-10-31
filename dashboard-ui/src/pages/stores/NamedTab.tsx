import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Anchor, Badge, Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconAlertTriangle, IconArrowRight, IconTrash } from "@tabler/icons-react";
import { z } from "zod";
import { api, qs, type Page } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { date, num } from "@/lib/format";
import { confirm, notifyOk } from "@/lib/notify";
import { ServerTable, EmptyState, useUrlSort, type Column } from "@/components/DataTable";
import { Toolbar, UrlSearch, useUrlParam } from "@/components/ui";
import { useLookups, type Named } from "./api";

type Kind = "banners" | "markets";
const one = (k: Kind) => (k === "banners" ? "banner" : "market");
const One = (k: Kind) => (k === "banners" ? "Banner" : "Market");

export function NamedTab({ kind, creating, onCreated }: { kind: Kind; creating: boolean; onCreated: () => void }) {
  const [q] = useUrlParam("q");
  const [sort, setSort] = useUrlSort({ sort: "name", dir: "asc" });
  const [editing, setEditing] = useState<Named | "new" | null>(null);
  useEffect(() => { if (creating) setEditing("new"); }, [creating]);
  const columns: Column<Named>[] = [
    { id: "name", header: One(kind), sort: "name", cell: (r) => <Group gap={6} wrap="nowrap"><Text fw={700} size="sm">{r.name}</Text>{r.edited_at && <Badge size="xs" color="yellow" variant="light">edited</Badge>}</Group> },
    { id: "id", header: "ID", sort: "id", width: 110, cell: (r) => r.id },
    { id: "stores", header: "Stores", sort: "stores", width: 140, align: "right", cell: (r) => r.stores
      ? <Anchor component={Link} to={`/stores/stores?${kind === "banners" ? "banner" : "market"}=${r.id}`} fw={700} size="sm">{num(r.stores)}</Anchor> : <Text span size="sm" c="dark.1">0</Text> },
    { id: "edited", header: "Edited here", sort: "edited", width: 150, cell: (r) => (r.edited_at ? date(r.edited_at) : <Text span size="sm" c="dark.1">-</Text>) },
  ];
  return (
    <>
      <Toolbar><UrlSearch placeholder={`${One(kind)} name or ID`} /></Toolbar>
      <ServerTable<Named> queryKey={["stores", kind, q]} fetchPage={(p) => api<Page<Named>>(`/${kind}` + qs({ q, ...p }))}
        columns={columns} sort={sort} onSort={setSort} rowKey={(r) => r.id} onRowClick={setEditing} pageSize={200}
        summary={(t) => `${num(t)} ${t === 1 ? one(kind) : kind}`}
        empty={<EmptyState title={q ? `No ${one(kind)} matches` : `No ${kind} yet`}>{q ? "Try another name." : `Add a ${one(kind)}, or import the stores from seed data.`}</EmptyState>} />
      <NamedDialog kind={kind} item={editing} onClose={() => { setEditing(null); if (creating) onCreated(); }} />
    </>
  );
}

function NamedDialog({ kind, item, onClose }: { kind: Kind; item: Named | "new" | null; onClose: () => void }) {
  const can = useCan();
  const qc = useQueryClient();
  const isNew = item === "new";
  const form = useForm({ initialValues: { name: "" }, validate: zodResolver(z.object({ name: z.string().trim().min(1, "Enter a name").max(120) })) });
  const [reassign, setReassign] = useState<Named | null>(null);
  useEffect(() => { const v = { name: item && item !== "new" ? item.name : "" }; form.setValues(v); form.resetDirty(v); }, [item]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: (name: string) => isNew ? api(`/${kind}`, { body: { name } }) : api(`/${kind}/${(item as Named).id}`, { method: "PUT", body: { name } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["stores"] }); notifyOk(`${One(kind)} saved`); onClose(); },
  });
  const del = useMutation({
    mutationFn: (n: Named) => api(`/${kind}/${n.id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["stores"] }); notifyOk(`${One(kind)} deleted`); onClose(); },
  });
  const askDelete = async () => {
    const n = item as Named;
    // check again: stores may have been assigned since the list loaded
    const { stores } = await api<{ stores: number }>(`/${kind}/${n.id}/usage`);
    if (stores) { setReassign({ ...n, stores }); return; }
    if (await confirm({ title: `Delete ${one(kind)} "${n.name}"?`, danger: true, confirmLabel: `Delete ${one(kind)}`,
      body: `No store uses it. It is removed from profile.data's ${One(kind)} table with the next catalog build.` })) del.mutate(n);
  };
  return (
    <>
      <Modal opened={item !== null && !reassign} onClose={onClose} title={<Text fw={800}>{isNew ? `Add a ${one(kind)}` : `${One(kind)} ${(item as Named | null)?.id ?? ""}`}</Text>}>
        <form onSubmit={form.onSubmit((v) => save.mutate(v.name))}>
          <Stack>
            <TextInput label="Name" data-autofocus disabled={!can()} {...form.getInputProps("name")} />
            {!isNew && item && <Text size="sm">{num((item as Named).stores)} stores use this {one(kind)}.</Text>}
            {can() && (
              <Group justify="space-between">
                {!isNew ? <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={askDelete} loading={del.isPending}>Delete</Button> : <span />}
                <Group><Button variant="default" onClick={onClose}>Cancel</Button>
                  <Button type="submit" loading={save.isPending} disabled={!form.isDirty()}>{isNew ? "Add" : "Save"}</Button></Group>
              </Group>
            )}
          </Stack>
        </form>
      </Modal>
      <ReassignDialog kind={kind} item={reassign} onClose={() => setReassign(null)} onDone={() => { setReassign(null); onClose(); }} />
    </>
  );
}

/** "In use" popup: Reassign (then choose where the stores go, then delete) or Cancel. */
function ReassignDialog({ kind, item, onClose, onDone }: { kind: Kind; item: Named | null; onClose: () => void; onDone: () => void }) {
  const qc = useQueryClient();
  const lookups = useLookups();
  const [step, setStep] = useState<"inuse" | "pick">("inuse");
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => { setStep("inuse"); setTarget(null); }, [item]);
  const run = useMutation({
    mutationFn: () => api<{ moved: number }>(`/${kind}/${item!.id}` + qs(target === "none" ? { clear: true } : { reassign_to: target }), { method: "DELETE" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["stores"] });
      notifyOk(`Moved ${num(r.moved)} stores and deleted "${item!.name}"`);
      onDone();
    },
  });
  const options = (kind === "banners" ? lookups.data?.banners : lookups.data?.markets) || [];
  const targetName = target === "none" ? `no ${one(kind)}` : options.find((o) => String(o.id) === target)?.name;
  return (
    <Modal opened={!!item} onClose={onClose} size="lg" title={<Group gap={8}><IconAlertTriangle size={20} color="var(--mantine-color-yellow-4)" /><Text fw={800}>{One(kind)} in use</Text></Group>}>
      {item && step === "inuse" && (
        <Stack>
          <Text><b>{item.name}</b> is used by <Anchor component={Link} to={`/stores/stores?${kind === "banners" ? "banner" : "market"}=${item.id}`} fw={700} onClick={onClose}>{num(item.stores)} stores</Anchor>.
            To delete it, those stores have to move to another {one(kind)} first.</Text>
          <Group justify="flex-end"><Button variant="default" onClick={onClose}>Cancel</Button>
            <Button rightSection={<IconArrowRight size={16} />} onClick={() => setStep("pick")}>Reassign</Button></Group>
        </Stack>
      )}
      {item && step === "pick" && (
        <Stack>
          <Select label={`Move the ${num(item.stores)} stores to`} placeholder={`Pick a ${one(kind)}`} searchable limit={100} data-autofocus
            data={[{ value: "none", label: `No ${one(kind)}` }, ...options.filter((o) => o.id !== item.id).map((o) => ({ value: String(o.id), label: o.name }))]}
            value={target} onChange={setTarget} nothingFoundMessage="No match" />
          {target && <Alert color="red" variant="light">{num(item.stores)} stores move to <b>{targetName}</b>, then <b>{item.name}</b> is deleted. Kiosks get both with the next catalog build.</Alert>}
          <Group justify="space-between">
            <Button variant="subtle" color="gray" onClick={() => setStep("inuse")}>Back</Button>
            <Group><Button variant="default" onClick={onClose}>Cancel</Button>
              <Button color="red" disabled={!target} loading={run.isPending} onClick={() => run.mutate()}>Move stores and delete</Button></Group>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
