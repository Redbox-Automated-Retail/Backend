// The title editor (a modal): every field, OMDb lookup for new titles, box art, product groups, barcodes, raw Lua, delete.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Box, Button, Checkbox, Divider, FileInput, Grid, Group, Modal, NumberInput, Paper, Select, SimpleGrid, Skeleton, Stack, Tabs, TagsInput, Text, TextInput, Textarea,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconCheck, IconLink, IconPhoto, IconSearch, IconTrash, IconUpload } from "@tabler/icons-react";
import { api, qs } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { fromRedboxDate, num, toIsoDate } from "@/lib/format";
import { ErrorState, LocalTable } from "@/components/DataTable";
import { DateField } from "@/components/fields";
import { ArtBox, BARCODE_STATUS, familyGenres, GROUP_TYPES, isGameType, useCatalogStatus, useLookups, type Barcode, type Group_, type Lookups } from "./shared";
import { GroupDrawer } from "./GroupsTab";

export type TitleValue = {
  long_name?: string; sort_name?: string; product_type_id?: number; rating_id?: number | null; sub_platform_id?: number | null;
  number_of_players_text?: string; running_time?: string; studio?: string; starring?: string[]; directors?: string[];
  genres?: Record<string, boolean>; image_file?: string | null; description?: string; release_date?: string | null;
  national_street_date?: string | null; sort_date?: string | null; merchandise_date?: string | null; sellthru_date?: string | null;
  stars?: number; coming_soon_days?: number; closed_captioned?: boolean; sell_thru?: boolean;
  [k: string]: unknown;
};
export type TitleDetail = { product_id: number; lua: string; value: TitleValue; groups: Group_[]; barcodes: Barcode[]; barcode_count: number };

const DATES = [["release_date", "Release date"], ["national_street_date", "Street date"], ["sort_date", "Sort date"],
  ["merchandise_date", "Merchandise date"], ["sellthru_date", "Sell-thru date"]] as const;
type DateKey = (typeof DATES)[number][0];

type Values = {
  long_name: string; sort_name: string; product_type_id: string | null; rating_id: string | null; sub_platform_id: string | null;
  number_of_players_text: string; running_time: string; studio: string; stars: number | string; coming_soon_days: number | string;
  closed_captioned: boolean; sell_thru: boolean; starring: string[]; directors: string[]; description: string; genres: string[];
} & Record<DateKey, string | null>;

const schema = z.object({
  long_name: z.string().trim().min(1, "A title needs a name"),
  product_type_id: z.string({ invalid_type_error: "Pick a format or platform" }).min(1, "Pick a format or platform"),
  running_time: z.string().regex(/^(\d{1,2}:\d{2})?$/, "Hours and minutes, like 1:45"),
});

function initial(v: TitleValue): Values {
  const s = (x: unknown) => (x == null ? null : String(x));
  return {
    long_name: v.long_name || "", sort_name: v.sort_name || "", product_type_id: s(v.product_type_id ?? 1), rating_id: s(v.rating_id ?? 10),
    sub_platform_id: s(v.sub_platform_id), number_of_players_text: v.number_of_players_text || "", running_time: v.running_time || "",
    studio: v.studio || "", stars: v.stars ?? "", coming_soon_days: v.coming_soon_days ?? "", closed_captioned: !!v.closed_captioned,
    sell_thru: !!v.sell_thru, starring: v.starring || [], directors: v.directors || [], description: v.description || "",
    genres: Object.keys(v.genres || {}).filter((k) => v.genres?.[k]),
    ...(Object.fromEntries(DATES.map(([k]) => [k, toIsoDate(fromRedboxDate(v[k] as string | null))])) as Record<DateKey, string | null>),
  };
}

function toField(k: keyof Values, x: unknown): unknown {
  if (k === "product_type_id" || k === "rating_id" || k === "sub_platform_id") return x ? Number(x) : null;
  if (k === "stars" || k === "coming_soon_days") return x === "" || x == null ? 0 : Number(x);
  if (k === "genres") return (x as string[]).map(Number);
  return x;
}

export function TitleEditor({ pid, opened, onClose, onCreated, onDeleted }: {
  pid: number | null; opened: boolean; onClose: () => void; onCreated: (pid: number) => void; onDeleted: (pid: number) => void;
}) {
  const L = useLookups();
  const d = useQuery({ queryKey: ["catalog", "title", pid], queryFn: () => api<TitleDetail>(`/catalog/titles/${pid}`), enabled: opened && pid != null });
  const [tab, setTab] = useState<string | null>("details");
  useEffect(() => setTab("details"), [pid]);
  const title = pid == null ? "New title" : d.data ? `${d.data.value.long_name || "Title"} (${pid})` : `Title ${pid}`;
  return (
    <Modal opened={opened} onClose={onClose} size={1080} title={<Text fw={800} size="lg">{title}</Text>} centered={false} yOffset="4vh">
      <Grid gutter="lg">
        {pid != null && d.data && L.data && (
          <Grid.Col span={{ base: 12, sm: 3 }} visibleFrom="sm">
            <Box style={{ position: "sticky", top: 0 }}><ArtBox L={L.data} row={{ product_id: pid, long_name: d.data.value.long_name || "",
              product_type_id: Number(d.data.value.product_type_id), release_date: d.data.value.release_date || null, image_file: d.data.value.image_file || null }} big /></Box>
          </Grid.Col>
        )}
        <Grid.Col span={{ base: 12, sm: pid != null ? 9 : 12 }}>
      {!L.data || (pid != null && d.isPending) ? <Stack>{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} h={40} />)}</Stack>
        : pid != null && d.isError ? <ErrorState error={d.error} onRetry={() => d.refetch()} />
        : pid == null ? <DetailsForm key="new" pid={null} v={{}} L={L.data} onCreated={onCreated} />
        : (
          <Tabs value={tab} onChange={setTab}>
            <Tabs.List mb="md">
              <Tabs.Tab value="details" fw={600}>Details</Tabs.Tab>
              <Tabs.Tab value="art" fw={600}>Box art</Tabs.Tab>
              <Tabs.Tab value="groups" fw={600}>Groups ({d.data!.groups.length})</Tabs.Tab>
              <Tabs.Tab value="barcodes" fw={600}>Barcodes ({num(d.data!.barcode_count)})</Tabs.Tab>
              <Tabs.Tab value="lua" fw={600}>Raw Lua</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="details">
              <DetailsForm key={`${pid}:${d.dataUpdatedAt}`} pid={pid} v={d.data!.value} L={L.data} onCreated={onCreated} />
              <DeleteTitle pid={pid} d={d.data!} onDeleted={onDeleted} />
            </Tabs.Panel>
            <Tabs.Panel value="art"><BoxArt pid={pid} d={d.data!} L={L.data} /></Tabs.Panel>
            <Tabs.Panel value="groups"><Groups pid={pid} d={d.data!} /></Tabs.Panel>
            <Tabs.Panel value="barcodes"><Barcodes pid={pid} d={d.data!} /></Tabs.Panel>
            <Tabs.Panel value="lua"><RawLua key={`${pid}:${d.dataUpdatedAt}`} pid={pid} lua={d.data!.lua} /></Tabs.Panel>
          </Tabs>
        )}
        </Grid.Col>
      </Grid>
    </Modal>
  );
}

// ---------------------------------------------------------------------------------------------- details

function DetailsForm({ pid, v, L, onCreated }: { pid: number | null; v: TitleValue; L: Lookups; onCreated: (pid: number) => void }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const status = useCatalogStatus();
  const form = useForm<Values>({ initialValues: initial(v), validate: zodResolver(schema) });
  const [poster, setPoster] = useState<string | null>(null);
  const [extra, setExtra] = useState<Record<string, unknown>>({});   // OMDb fields the form has no input for (box office)
  const [omdb, setOmdb] = useState({ title: "", year: "", imdb_id: "" });

  const lookup = useMutation({
    mutationFn: () => api<{ omdb: { Title: string; Year: string }; fields: TitleValue & { genres?: number[] }; poster?: string }>("/catalog/omdb", { body: omdb }),
    onSuccess: (r) => {
      const f = r.fields;
      const vals: Partial<Values> = {};
      const more: Record<string, unknown> = {};
      for (const [k, x] of Object.entries(f)) {
        if (k === "genres") vals.genres = ((x as number[]) || []).map(String);
        else if (DATES.some(([dk]) => dk === k)) (vals as Record<string, unknown>)[k] = toIsoDate(fromRedboxDate(x as string));
        else if (k === "rating_id") vals.rating_id = x == null ? null : String(x);
        else if (k in form.values) (vals as Record<string, unknown>)[k] = x ?? "";
        else more[k] = x;
      }
      form.setValues(vals);
      setExtra(more);
      setPoster(r.poster && r.poster !== "N/A" ? r.poster : null);
      notifyOk(`OMDb: ${r.omdb.Title} (${r.omdb.Year})`);
    },
  });

  const save = useMutation({
    mutationFn: async (fields: Record<string, unknown>) => {
      const r = await api<{ product_id: number }>("/catalog/titles", { body: { product_id: pid, fields } });
      if (pid == null && poster) {
        const fd = new FormData();
        fd.append("url", poster);
        await api(`/catalog/titles/${r.product_id}/image`, { form: fd });
      }
      return r;
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["catalog"] });
      notifyOk(pid == null ? `Created title ${r.product_id}` : "Saved. Publish the catalog to send it to kiosks.");
      if (pid == null) onCreated(r.product_id);
    },
  });

  const submit = form.onSubmit((vals) => {
    const fields: Record<string, unknown> = pid == null ? { ...extra } : {};
    for (const k of Object.keys(vals) as (keyof Values)[]) {
      const x = toField(k, vals[k]);
      if (pid != null ? form.isDirty(k) : x !== "" && x != null && !(Array.isArray(x) && !x.length)) fields[k] = x;
    }
    save.mutate(fields);
  });

  const game = isGameType(L, form.values.product_type_id);
  const fam = game ? 2 : 1;
  const famName = (id: number) => L.families.find((f) => Number(f.product_family_id) === id)?.product_family_name || (id === 2 ? "Games" : id === 1 ? "Movies" : `Family ${id}`);
  const typeGroups = [...new Set(L.types.map((t) => Number(t.product_family_id)))].map((f) => ({
    group: famName(f), items: L.types.filter((t) => Number(t.product_family_id) === f).map((t) => ({ value: String(t.product_type_id), label: t.product_type_name })),
  }));
  const ratings = L.ratings.filter((r) => Number(r.product_family_id) === fam || String(r.rating_id) === form.values.rating_id)
    .map((r) => ({ value: String(r.rating_id), label: r.name }));
  const ro = !can;

  return (
    <Stack gap="md">
      {pid == null && can && (
        <Paper p="sm" bg="dark.6" withBorder>
          <Text fw={700} mb={4}>Fill from OMDb</Text>
          {status.data && !status.data.omdb ? <Text size="sm">Set OMDB_API_KEY on the server to look movies up here.</Text> : (
            <Group gap="xs" align="flex-end" wrap="wrap">
              <TextInput label="Title" value={omdb.title} onChange={(e) => setOmdb({ ...omdb, title: e.currentTarget.value })} style={{ flex: 1 }} miw={160} />
              <TextInput label="Year" w={80} value={omdb.year} onChange={(e) => setOmdb({ ...omdb, year: e.currentTarget.value })} />
              <TextInput label="or IMDb id" w={120} placeholder="tt0000000" value={omdb.imdb_id} onChange={(e) => setOmdb({ ...omdb, imdb_id: e.currentTarget.value })} />
              <Button variant="default" leftSection={<IconSearch size={16} />} loading={lookup.isPending} disabled={!omdb.title && !omdb.imdb_id} onClick={() => lookup.mutate()}>Look up</Button>
            </Group>
          )}
          {poster && (
            <Group mt="sm" gap="sm"><img src={poster} alt="" style={{ height: 120, borderRadius: 6 }} /><Text size="sm">This poster is stored as the box art when you create the title.</Text></Group>
          )}
        </Paper>
      )}
      <form onSubmit={submit}>
        <Stack gap="sm">
          <TextInput label="Title" withAsterisk disabled={ro} {...form.getInputProps("long_name")} />
          <TextInput label="Sort name" description={pid == null ? "Empty uses the title" : undefined} disabled={ro} {...form.getInputProps("sort_name")} />
          <SimpleGrid cols={2} spacing="sm">
            <Select label={game ? "Platform" : "Format"} data={typeGroups} searchable allowDeselect={false} disabled={ro} {...form.getInputProps("product_type_id")} />
            <Select label={game ? "ESRB rating" : "Rating"} data={ratings} disabled={ro} {...form.getInputProps("rating_id")} />
          </SimpleGrid>
          {game && (
            <SimpleGrid cols={2} spacing="sm">
              <Select label="Accessory" placeholder="none" clearable disabled={ro} {...form.getInputProps("sub_platform_id")}
                data={L.subplatforms.map((s) => ({ value: String(s.subplatform_id), label: s.subplatform_name }))} />
              <TextInput label="Players" placeholder="e.g. 1-4 players" disabled={ro} {...form.getInputProps("number_of_players_text")} />
            </SimpleGrid>
          )}
          <SimpleGrid cols={2} spacing="sm">
            {DATES.map(([k, l]) => (
              <DateField key={k} label={l} disabled={ro} value={form.values[k]} onChange={(x) => form.setFieldValue(k, x)}
                description={pid == null && k !== "sellthru_date" ? "Empty: today" : undefined} />
            ))}
          </SimpleGrid>
          <SimpleGrid cols={2} spacing="sm">
            <TextInput label={game ? "Publisher" : "Studio"} disabled={ro} {...form.getInputProps("studio")} />
            {!game && <TextInput label="Running time" placeholder="h:mm" disabled={ro} {...form.getInputProps("running_time")} />}
            <NumberInput label="Stars" min={0} max={5} allowDecimal={false} disabled={ro} {...form.getInputProps("stars")} />
            <NumberInput label="Coming soon days" min={0} allowDecimal={false} disabled={ro} {...form.getInputProps("coming_soon_days")} />
          </SimpleGrid>
          {!game && (
            <>
              <TagsInput label="Starring" placeholder="Add a name, press Enter" disabled={ro} {...form.getInputProps("starring")} />
              <TagsInput label="Directors" placeholder="Add a name, press Enter" disabled={ro} {...form.getInputProps("directors")} />
            </>
          )}
          <Textarea label="Description" autosize minRows={4} maxRows={12} disabled={ro} {...form.getInputProps("description")} />
          <Group gap="lg">
            <Checkbox label="Closed captioned" disabled={ro} {...form.getInputProps("closed_captioned", { type: "checkbox" })} />
            <Checkbox label="Sell-thru" disabled={ro} {...form.getInputProps("sell_thru", { type: "checkbox" })} />
          </Group>
          <Checkbox.Group label={game ? "Game genres" : "Genres"} value={form.values.genres} onChange={(x) => form.setFieldValue("genres", x)}>
            <SimpleGrid cols={3} spacing={6} mt={6}>
              {familyGenres(L, fam, form.values.genres).map((g) => <Checkbox key={g.genre_id} size="xs" value={String(g.genre_id)} label={g.genre_name} disabled={ro} />)}
            </SimpleGrid>
          </Checkbox.Group>
          {can && (
            <Group justify="flex-end" mt="sm">
              <Button type="submit" leftSection={<IconCheck size={16} />} loading={save.isPending} disabled={pid != null && !form.isDirty()}>
                {pid == null ? "Create title" : "Save"}
              </Button>
            </Group>
          )}
        </Stack>
      </form>
    </Stack>
  );
}

function DeleteTitle({ pid, d, onDeleted }: { pid: number; d: TitleDetail; onDeleted: (pid: number) => void }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: (barcodes: boolean) => api<{ barcodes_removed: number }>(`/catalog/titles/${pid}/delete`, { body: { barcodes } }),
    onSuccess: (r) => {
      notifyOk(`Deleted title ${pid}${r.barcodes_removed ? ` and ${num(r.barcodes_removed)} barcodes` : ""}`);
      qc.invalidateQueries({ queryKey: ["catalog"] });
      onDeleted(pid);
    },
  });
  if (!can) return null;
  const ask = async () => {
    let withBarcodes = false;
    const ok = await confirm({
      title: `Delete ${d.value.long_name || "title"} (${pid})?`, danger: true, confirmLabel: "Delete title",
      body: (
        <Stack gap="sm">
          <Text size="sm">The title leaves the catalog, its product groups (a group left empty is deleted) and the group cross-reference tables. Its box art goes too unless another title uses it. Kiosks see this after the next publish.</Text>
          {d.barcode_count > 0 && (
            <Checkbox label={`Also delete its ${num(d.barcode_count)} barcodes (otherwise they stay, pointing at a missing title)`} onChange={(e) => { withBarcodes = e.currentTarget.checked; }} />
          )}
        </Stack>
      ),
    });
    if (ok) del.mutate(withBarcodes);
  };
  return (
    <>
      <Divider my="lg" />
      <Group justify="space-between">
        <Text size="sm" fw={600}>Remove this title from the catalog.</Text>
        <Button color="red" variant="light" leftSection={<IconTrash size={16} />} loading={del.isPending} onClick={ask}>Delete title</Button>
      </Group>
    </>
  );
}

// ---------------------------------------------------------------------------------------------- box art

function BoxArt({ pid, d, L }: { pid: number; d: TitleDetail; L: Lookups }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const set = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      if (file) fd.append("file", file); else fd.append("url", url.trim());
      return api<{ image_file: string }>(`/catalog/titles/${pid}/image`, { form: fd });
    },
    onSuccess: (r) => { notifyOk(`Box art set (${r.image_file})`); setFile(null); setUrl(""); qc.invalidateQueries({ queryKey: ["catalog"] }); },
  });
  return (
    <Stack gap="md">
      <Group align="flex-start" gap="lg" wrap="nowrap">
        <Box w={170} style={{ borderRadius: 10, overflow: "hidden", flexShrink: 0 }}><ArtBox L={L} row={{ ...d.value, product_id: pid }} big /></Box>
        <Stack gap={4}>
          <Text fw={700}>{d.value.image_file ? "Current box art" : "No box art yet"}</Text>
          {d.value.image_file && <Text size="sm" style={{ wordBreak: "break-all" }}>{d.value.image_file}</Text>}
          <Text size="sm" c="dark.1">JPEG, PNG or GIF. Stored in the Cache table under its SHA-1 and sent with the catalog.</Text>
        </Stack>
      </Group>
      {can && (
        <Paper p="sm" bg="dark.6" withBorder>
          <Stack gap="sm">
            <FileInput label="Upload an image" placeholder="Pick a file" accept="image/*" clearable leftSection={<IconPhoto size={16} />}
              value={file} onChange={(f) => { setFile(f); if (f) setUrl(""); }} />
            <TextInput label="or an image URL" placeholder="https://..." leftSection={<IconLink size={16} />} value={url}
              onChange={(e) => { setUrl(e.currentTarget.value); if (e.currentTarget.value) setFile(null); }} />
            <Group justify="flex-end">
              <Button leftSection={<IconUpload size={16} />} disabled={!file && !url.trim()} loading={set.isPending} onClick={() => set.mutate()}>Set box art</Button>
            </Group>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}

// ------------------------------------------------------------------------------------------- product groups

function Groups({ pid, d }: { pid: number; d: TitleDetail }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const [open, setOpen] = useState<Group_ | null>(null);
  const [other, setOther] = useState<string | number>("");
  const rollup = useMutation({
    mutationFn: async () => {
      const o = Number(other);
      const mine = d.groups.find((g) => Number(g.value.product_group_type_id) === 1);
      const theirs = mine ? null : (await api<Group_[]>("/catalog/groups" + qs({ product_id: o }))).find((g) => Number(g.value.product_group_type_id) === 1);
      const g = mine || theirs;
      const ids = g ? [...new Set([...(g.value.product_id_list || []).map(Number), pid, o])] : [o, pid];
      return api("/catalog/groups", { body: { product_group_id: g ? g.product_group_id : null, name: g ? g.value.product_group_name : d.value.long_name, type: 1, product_ids: ids } });
    },
    onSuccess: () => { notifyOk(`Rolled up with ${other}`); setOther(""); qc.invalidateQueries({ queryKey: ["catalog"] }); },
  });
  return (
    <Stack gap="md">
      <LocalTable rows={d.groups} rowKey={(g) => g.product_group_id} onRowClick={setOpen}
        empty={<Text size="sm" p="md">In no product group. Roll it up with another edition below.</Text>}
        columns={[
          { id: "id", header: "Group", cell: (g) => g.product_group_id, value: (g) => g.product_group_id, width: 80 },
          { id: "name", header: "Name", cell: (g) => g.value.product_group_name, value: (g) => g.value.product_group_name },
          { id: "type", header: "Type", cell: (g) => GROUP_TYPES.find((t) => t.value === String(g.value.product_group_type_id))?.label.split(" - ")[1] || g.value.product_group_type_id, value: (g) => g.value.product_group_type_id },
          { id: "titles", header: "Titles", cell: (g) => (g.value.product_id_list || []).join(", "), width: 160 },
        ]} />
      {can && (
        <Paper p="sm" bg="dark.6" withBorder>
          <Text fw={700}>Roll up with another edition</Text>
          <Text size="sm" mb="xs">Groups this title with the DVD / Blu-ray / 4K edition of the same film (a type 1 group; joins an existing one when either title has it).</Text>
          <Group align="flex-end" gap="sm">
            <NumberInput label="Product id of the other edition" value={other} onChange={setOther} allowDecimal={false} min={1} style={{ flex: 1 }} />
            <Button loading={rollup.isPending} disabled={!other || Number(other) === pid} onClick={() => rollup.mutate()}>Roll up</Button>
          </Group>
        </Paper>
      )}
      <GroupDrawer group={open} opened={!!open} onClose={() => setOpen(null)} />
    </Stack>
  );
}

// ------------------------------------------------------------------------------------------------ barcodes

function Barcodes({ pid, d }: { pid: number; d: TitleDetail }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const add = useMutation({
    mutationFn: () => api("/catalog/barcodes", { body: { barcode: code.trim(), product_id: pid } }),
    onSuccess: () => { notifyOk(`Barcode ${code.trim()} added`); setCode(""); qc.invalidateQueries({ queryKey: ["catalog"] }); },
  });
  const del = useMutation({
    mutationFn: (b: string) => api("/catalog/barcodes/delete", { body: { barcode: b } }),
    onSuccess: (_r, b) => { notifyOk(`Barcode ${b} deleted`); qc.invalidateQueries({ queryKey: ["catalog"] }); },
  });
  const ask = async (b: Barcode) => {
    if (await confirm({ title: `Delete barcode ${b.barcode}?`, danger: true, confirmLabel: "Delete barcode",
      body: "Kiosks stop recognising this disc as this title after the next publish. Its rental count goes with it." })) del.mutate(b.barcode);
  };
  return (
    <Stack gap="md">
      {can && (
        <Group align="flex-end" gap="sm">
          <TextInput label="Add a barcode" placeholder="Digits" value={code} onChange={(e) => setCode(e.currentTarget.value)} style={{ flex: 1 }}
            error={code && !/^\d+$/.test(code.trim()) ? "Digits only" : undefined}
            onKeyDown={(e) => { if (e.key === "Enter" && /^\d+$/.test(code.trim())) add.mutate(); }} />
          <Button loading={add.isPending} disabled={!/^\d+$/.test(code.trim())} onClick={() => add.mutate()}>Add barcode</Button>
        </Group>
      )}
      {d.barcode_count > d.barcodes.length && <Alert color="gray" variant="light">Showing the first {num(d.barcodes.length)} of {num(d.barcode_count)} barcodes. Find others on the Barcodes tab.</Alert>}
      <LocalTable rows={d.barcodes} rowKey={(b) => b.barcode} maxHeight={420}
        empty={<Text size="sm" p="md">No barcodes for this title.</Text>}
        columns={[
          { id: "barcode", header: "Barcode", cell: (b) => b.barcode, value: (b) => b.barcode },
          { id: "status", header: "Status", cell: (b) => BARCODE_STATUS[b.status] ?? b.status, value: (b) => b.status },
          { id: "rentals", header: "Rentals", cell: (b) => num(b.rental_count), value: (b) => b.rental_count, align: "right" },
          { id: "source", header: "Source", cell: (b) => b.source, value: (b) => b.source },
          ...(can ? [{ id: "x", header: "", cell: (b: Barcode) => <Button size="compact-xs" color="red" variant="subtle" onClick={() => ask(b)}>Delete</Button> }] : []),
        ]} />
    </Stack>
  );
}

// ------------------------------------------------------------------------------------------------ raw Lua

function RawLua({ pid, lua }: { pid: number; lua: string }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const [text, setText] = useState(lua);
  const save = useMutation({
    mutationFn: () => api("/catalog/titles", { body: { product_id: pid, lua: text } }),
    onSuccess: () => { notifyOk("Lua saved"); qc.invalidateQueries({ queryKey: ["catalog"] }); },
  });
  return (
    <Stack gap="sm">
      <Text size="sm">The title's row exactly as profile.data stores it: a Lua table literal the kiosk engine reads. Fields the form doesn't show can be edited here.</Text>
      <Textarea value={text} onChange={(e) => setText(e.currentTarget.value)} autosize minRows={12} maxRows={30} readOnly={!can}
        styles={{ input: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12.5 } }} aria-label="Lua value" />
      {can && <Group justify="flex-end"><Button loading={save.isPending} disabled={text === lua || !text.trim()} onClick={() => save.mutate()}>Save Lua</Button></Group>}
    </Stack>
  );
}
