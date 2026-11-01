// Product groups (ProductGroup): title rollups (DVD / Blu-ray / 4K of one film) and multi-disc sets.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Drawer, Group, Select, Stack, TagsInput, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { api, qs, type Page } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { num } from "@/lib/format";
import { EmptyState, ServerTable, useUrlSort } from "@/components/DataTable";
import { Toolbar, UrlSearch, useUrlParam } from "@/components/ui";
import { GROUP_TYPES, type Group_ } from "./shared";

export function GroupsTab() {
  const can = useCan()("change");
  const [q] = useUrlParam("q");
  const [open, setOpen] = useUrlParam("group");
  const [sort, setSort] = useUrlSort({ sort: "id", dir: "desc" });
  const [current, setCurrent] = useState<Group_ | null>(null);
  // a group opened from a link (?group=id) is looked up once
  const linked = useQuery({ queryKey: ["catalog", "groups", "one", open], enabled: !!open && open !== "new" && current?.product_group_id !== Number(open),
    queryFn: async () => (await api<Page<Group_>>("/catalog/groups" + qs({ q: open, paged: true, limit: 5 }))).rows.find((g) => String(g.product_group_id) === open) || null });
  const group = current && String(current.product_group_id) === open ? current : linked.data || null;
  return (
    <>
      <Toolbar>
        <UrlSearch placeholder="Group name or id" />
        {can && <Button leftSection={<IconPlus size={16} />} onClick={() => { setCurrent(null); setOpen("new"); }}>New group</Button>}
      </Toolbar>
      <ServerTable<Group_> queryKey={["catalog", "groups", q || ""]} sort={sort} onSort={setSort} rowKey={(g) => g.product_group_id}
        fetchPage={(p) => api<Page<Group_>>("/catalog/groups" + qs({ q, paged: true, ...p }))}
        onRowClick={(g) => { setCurrent(g); setOpen(String(g.product_group_id)); }}
        summary={(t) => `${num(t)} product groups`}
        empty={<EmptyState title={q ? "No group matches" : "No product groups"}>Groups roll the editions of one film together, or hold the discs of a set. Create one with New group or from a title's full editor.</EmptyState>}
        columns={[
          { id: "id", header: "Group", sort: "id", cell: (g) => g.product_group_id, width: 90 },
          { id: "name", header: "Name", sort: "name", cell: (g) => g.value.product_group_name },
          { id: "type", header: "Type", sort: "type", cell: (g) => GROUP_TYPES.find((t) => t.value === String(g.value.product_group_type_id))?.label || g.value.product_group_type_id, width: 200 },
          { id: "n", header: "Titles", cell: (g) => (g.value.product_id_list || []).length, align: "right", width: 80 },
          { id: "ids", header: "Product ids", cell: (g) => (g.value.product_id_list || []).join(", "), width: 300 },
        ]} />
      <GroupDrawer group={group} isNew={open === "new"} opened={!!open && (open === "new" || !!group)} onClose={() => setOpen(null)} />
    </>
  );
}
const schema = z.object({
  name: z.string().trim().min(1, "Give the group a name"),
  type: z.string().min(1),
  ids: z.array(z.string().regex(/^\d+$/, "Product ids are numbers")).min(1, "Add at least one product id"),
});
type Values = z.infer<typeof schema>;

/** Create / edit / delete a product group. */
export function GroupDrawer({ group, isNew, opened, onClose }: { group: Group_ | null; isNew?: boolean; opened: boolean; onClose: () => void }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const init = (): Values => ({
    name: group?.value.product_group_name || "", type: String(group?.value.product_group_type_id ?? 1),
    ids: (group?.value.product_id_list || []).map(String),
  });
  const form = useForm<Values>({ initialValues: init(), validate: zodResolver(schema) });
  useEffect(() => { const v = init(); form.setInitialValues(v); form.setValues(v); form.clearErrors(); }, [group?.product_group_id, opened]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: (v: Values) => api<{ product_group_id: number }>("/catalog/groups", {
      body: { product_group_id: group?.product_group_id ?? null, name: v.name.trim(), type: Number(v.type), product_ids: [...new Set(v.ids.map(Number))] },
    }),
    onSuccess: (r) => { notifyOk(group ? "Group saved" : `Group ${r.product_group_id} created`); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
  });
  const del = useMutation({
    mutationFn: () => api(`/catalog/groups/${group!.product_group_id}/delete`, { body: {} }),
    onSuccess: () => { notifyOk("Group deleted"); qc.invalidateQueries({ queryKey: ["catalog"] }); onClose(); },
  });
  const ask = async () => {
    if (await confirm({ title: `Delete group ${group!.product_group_id}?`, danger: true, confirmLabel: "Delete group",
      body: `"${group!.value.product_group_name}" and its cross-reference rows go; the ${(group!.value.product_id_list || []).length} titles in it stay.` })) del.mutate();
  };
  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="lg"
      title={<Text fw={800} size="lg">{group ? `Product group ${group.product_group_id}` : isNew ? "New product group" : "Product group"}</Text>}>
      <form onSubmit={form.onSubmit((v) => save.mutate(v))}>
        <Stack gap="sm">
          <TextInput label="Name" withAsterisk disabled={!can} {...form.getInputProps("name")} />
          <Select label="Type" data={GROUP_TYPES} allowDeselect={false} disabled={!can} {...form.getInputProps("type")} />
          <TagsInput label="Product ids" description="Type a product id and press Enter" withAsterisk disabled={!can} splitChars={[",", " "]} {...form.getInputProps("ids")} />
          {can && (
            <Group justify="space-between" mt="md">
              {group ? <Button color="red" variant="light" leftSection={<IconTrash size={16} />} loading={del.isPending} onClick={ask}>Delete group</Button> : <span />}
              <Button type="submit" loading={save.isPending} disabled={!form.isDirty()}>{group ? "Save" : "Create group"}</Button>
            </Group>
          )}
        </Stack>
      </form>
    </Drawer>
  );
}
