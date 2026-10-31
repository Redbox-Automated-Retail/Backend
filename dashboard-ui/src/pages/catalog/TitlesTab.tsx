// Titles: a poster grid with filter chips (formats, games per platform, coming soon, changed, no art), loaded in pages,
// and a details pane with quick edit. The full editor is a drawer (?edit=<product id> or ?edit=new).
import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import {
  ActionIcon, Anchor, Badge, Box, Button, Checkbox, Chip, Grid, Group, Paper, Select, SimpleGrid, Skeleton, Stack, TagsInput, Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconCheck, IconEdit, IconPlus, IconSortAscending, IconSortDescending } from "@tabler/icons-react";
import { api, qs } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { notifyOk } from "@/lib/notify";
import { fromRedboxDate, num, toIsoDate } from "@/lib/format";
import { EmptyState, ErrorState } from "@/components/DataTable";
import { Facts, UrlSearch, useUrlParam } from "@/components/ui";
import { DateField } from "@/components/fields";
import { ArtBox, familyGenres, isGameType, ratingName, typeInfo, useLookups, type Lookups } from "./shared";
import { TitleEditor, type TitleDetail } from "./TitleEditor";

type BrowseRow = {
  product_id: number; long_name: string; product_type_id: number; rating_id: string | null; release_date: string | null;
  image_file: string | null; sub_platform_id: string | null; discs: number; kiosks: number; changed: boolean;
};
type Browse = {
  total: number; rows: BrowseRow[]; stamped_at: string | null;
  counts: { total: number; changed: number; noart: number; soon: number };
  by_type: { product_type_id: number; n: number }[];
};

const PAGE = 60;

function chipParams(chip: string): Record<string, string> {
  if (chip === "newest") return { newest: "true" };
  if (chip === "games") return { family: "2" };
  if (chip === "soon") return { soon: "true" };
  if (chip === "changed") return { changed: "true" };
  if (chip === "noart") return { noart: "true" };
  if (/^[tg]\d+$/.test(chip)) return { types: chip.slice(1) };
  return {};
}

export function TitlesTab() {
  const L = useLookups();
  const can = useCan()("change");
  const [q] = useUrlParam("q");
  const [chipRaw, setChip] = useUrlParam("f");
  const chip = chipRaw || "all";
  const [sort, setSort] = useUrlParam("sort");
  const [dir, setDir] = useUrlParam("dir");
  const [sel, setSel] = useUrlParam("t");
  const [edit, setEdit] = useUrlParam("edit");

  const params = { q: q || "", ...chipParams(chip), sort: sort || "", dir: dir || "" };
  const browse = useInfiniteQuery({
    queryKey: ["catalog", "browse", params],
    queryFn: ({ pageParam }) => api<Browse>("/catalog/browse" + qs({ ...params, limit: PAGE, offset: pageParam })),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((a, p) => a + p.rows.length, 0);
      return loaded < last.total && last.rows.length ? loaded : undefined;
    },
    placeholderData: keepPreviousData,
  });
  const rows = useMemo(() => browse.data?.pages.flatMap((p) => p.rows) ?? [], [browse.data]);
  const first = browse.data?.pages[0];
  const total = first?.total ?? 0;

  // infinite scroll: load the next page when the end of the grid comes into view
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((e) => {
      if (e[0].isIntersecting && browse.hasNextPage && !browse.isFetchingNextPage) browse.fetchNextPage();
    }, { rootMargin: "400px" });
    io.observe(el);
    return () => io.disconnect();
  }, [browse.hasNextPage, browse.isFetchingNextPage, browse.fetchNextPage, rows.length]);

  const chips = useMemo(() => {
    if (!first || !L.data) return null;
    const games = first.by_type.filter((t) => isGameType(L.data, t.product_type_id));
    const movies = first.by_type.filter((t) => !isGameType(L.data, t.product_type_id));
    const inGames = chip === "games" || chip.startsWith("g");
    return [
      { k: "all", l: "All" }, { k: "newest", l: "New releases" },
      ...movies.slice(0, 4).map((t) => ({ k: "t" + t.product_type_id, l: `${typeInfo(L.data, t.product_type_id).product_type_name} · ${num(t.n)}` })),
      { k: "games", l: `Games · ${num(games.reduce((a, t) => a + Number(t.n), 0))}`, on: inGames },
      ...(inGames ? games.map((t) => ({ k: "g" + t.product_type_id, l: `${typeInfo(L.data, t.product_type_id).product_type_name} · ${num(t.n)}`, sub: true })) : []),
      { k: "soon", l: `Coming soon · ${num(first.counts.soon)}` },
      { k: "changed", l: `Changed since build · ${num(first.counts.changed)}` },
      { k: "noart", l: `No box art · ${num(first.counts.noart)}` },
    ] as { k: string; l: string; on?: boolean; sub?: boolean }[];
  }, [first, L.data, chip]);

  const refreshing = browse.isFetching && !browse.isFetchingNextPage && !browse.isPending;
  const selPid = sel ? Number(sel) : null;
  const editPid = edit && edit !== "new" ? Number(edit) : null;

  return (
    <Grid gutter="lg">
      <Grid.Col span={{ base: 12, lg: 8 }}>
        <Group gap="sm" mb="sm" wrap="wrap">
          <UrlSearch placeholder="Title, product id or barcode" />
          <Select w={150} aria-label="Sort by" value={sort || ""} onChange={(v) => setSort(v || null)} allowDeselect={false}
            data={[{ value: "", label: "Default order" }, { value: "title", label: "Title" }, { value: "release", label: "Release date" }, { value: "id", label: "Product id" }]} />
          <Tooltip label={dir === "asc" ? "Ascending" : "Descending"}>
            <ActionIcon variant="default" size={36} aria-label="Sort direction" onClick={() => setDir(dir === "asc" ? "desc" : "asc")}>
              {dir === "asc" ? <IconSortAscending size={18} /> : <IconSortDescending size={18} />}
            </ActionIcon>
          </Tooltip>
          {can && <Button leftSection={<IconPlus size={16} />} onClick={() => setEdit("new")}>New title</Button>}
        </Group>
        <Group gap={8} mb="md" wrap="wrap" mih={30}>
          {chips ? chips.map((c) => (
            <Chip key={c.k} size="sm" radius="xl" variant={c.sub ? "light" : "filled"} color={c.sub ? "grape" : "rubine"}
              checked={chip === c.k || !!c.on} onChange={() => setChip(c.k === chip && c.k !== "all" ? null : c.k === "all" ? null : c.k)}>
              {c.l}
            </Chip>
          )) : Array.from({ length: 8 }, (_, i) => <Skeleton key={i} h={28} w={80 + (i % 3) * 30} radius="xl" />)}
        </Group>
        <Group justify="space-between" mb="xs" mih={22}>
          <Text size="sm" fw={600}>{browse.isPending ? "Loading titles..." : browse.isError ? "" : `${num(total)} ${total === 1 ? "title" : "titles"}${rows.length < total ? ` · showing ${num(rows.length)}` : ""}`}</Text>
          {refreshing && <Text size="xs" fw={600}>Updating...</Text>}
        </Group>
        {browse.isError ? <Paper bg="dark.7"><ErrorState error={browse.error} onRetry={() => browse.refetch()} /></Paper> : (
          <Box style={{ opacity: refreshing ? 0.6 : 1, transition: "opacity .15s" }}>
            <div className="rb-posters" aria-busy={browse.isFetching}>
              {browse.isPending ? <PosterSkeletons n={18} /> : rows.map((r) => (
                <Poster key={r.product_id} r={r} L={L.data} selected={selPid === r.product_id} onClick={() => setSel(String(r.product_id))} />
              ))}
              {browse.isFetchingNextPage && <PosterSkeletons n={6} />}
            </div>
            {!browse.isPending && !rows.length && (
              <Paper bg="dark.7" mt="sm">
                {first?.counts.total ? <EmptyState title="No title matches">Try another search or filter.</EmptyState> : (
                  <EmptyState title="No catalog yet">Import one under <Anchor component={Link} to="/admin">System administration</Anchor> (catalog import), then edit and publish it here.</EmptyState>
                )}
              </Paper>
            )}
            <div ref={sentinel} />
            {browse.hasNextPage && !browse.isFetchingNextPage && (
              <Group justify="center" mt="md"><Button variant="default" onClick={() => browse.fetchNextPage()}>Show more ({num(total - rows.length)} left)</Button></Group>
            )}
          </Box>
        )}
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 4 }}>
        <Box style={{ position: "sticky", top: 16 }}>
          {selPid ? <TitleDetails pid={selPid} L={L.data} changed={rows.find((r) => r.product_id === selPid)?.changed} onFull={() => setEdit(String(selPid))} />
            : <Paper bg="dark.7" p="lg"><EmptyState title="Pick a title">Its details and a quick editor show here. Use the full editor for box art, barcodes, product groups and the raw Lua.</EmptyState></Paper>}
        </Box>
      </Grid.Col>
      <TitleEditor pid={editPid} opened={!!edit} onClose={() => setEdit(null)}
        onCreated={(pid) => { setEdit(String(pid)); setSel(String(pid)); }} onDeleted={(pid) => { setEdit(null); if (selPid === pid) setSel(null); }} />
    </Grid>
  );
}

function PosterSkeletons({ n }: { n: number }) {
  return <>{Array.from({ length: n }, (_, i) => (
    <div key={i} className="rb-poster" style={{ cursor: "default" }} aria-hidden>
      <Skeleton style={{ aspectRatio: "2 / 3" }} h="auto" radius={0} />
      <Box p={8}><Skeleton h={12} w="85%" /><Skeleton h={10} w="60%" mt={6} /></Box>
    </div>
  ))}</>;
}

function Poster({ r, L, selected, onClick }: { r: BrowseRow; L?: Lookups; selected: boolean; onClick: () => void }) {
  return (
    <button type="button" className="rb-poster" data-selected={selected || undefined} onClick={onClick} aria-pressed={selected}>
      <ArtBox L={L} row={r} />
      <Box p={8}>
        <Text size="sm" fw={700} lineClamp={2} lh={1.25}>{r.long_name}</Text>
        <Text size="xs" fw={500} c="dark.1" mt={2}>
          {[ratingName(L, r.rating_id), r.release_date?.slice(0, 4), r.discs ? `${num(r.discs)} in kiosks` : "not stocked"].filter(Boolean).join(" · ")}
        </Text>
        {r.changed && <Badge size="xs" color="pink" variant="light" mt={4}>changed</Badge>}
      </Box>
    </button>
  );
}

// ------------------------------------------------------------------------------------------- details pane

function TitleDetails({ pid, L, changed, onFull }: { pid: number; L?: Lookups; changed?: boolean; onFull: () => void }) {
  const d = useQuery({ queryKey: ["catalog", "title", pid], queryFn: () => api<TitleDetail>(`/catalog/titles/${pid}`) });
  if (d.isError) return <Paper bg="dark.7"><ErrorState error={d.error} onRetry={() => d.refetch()} /></Paper>;
  if (d.isPending || !L) {
    return (
      <Paper bg="dark.7" p="md">
        <Skeleton mx="auto" w={180} style={{ aspectRatio: "2 / 3" }} h="auto" />
        <Skeleton h={22} w="70%" mt="md" /><Skeleton h={14} w="50%" mt={8} />
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} h={34} mt="sm" />)}
      </Paper>
    );
  }
  const v = d.data.value || {};
  const game = isGameType(L, v.product_type_id);
  return (
    <Paper bg="dark.7" p="md">
      <Box mx="auto" w={180} style={{ borderRadius: 10, overflow: "hidden" }}><ArtBox L={L} row={{ ...v, product_id: pid }} big /></Box>
      <Group mt="md" justify="space-between" align="flex-start" wrap="nowrap">
        <Title order={3} fz={18}>{v.long_name}</Title>
        {changed && <Badge color="pink" variant="light">changed</Badge>}
      </Group>
      <Text size="sm" fw={600} c="dark.1" mt={4} mb="sm">Product {pid} · {num(d.data.groups.length)} groups · {num(d.data.barcode_count)} barcodes</Text>
      <Facts cols={2} items={game ? [
        ["Platform", typeInfo(L, v.product_type_id).product_type_name],
        ["Publisher", v.studio || "-"],
        ["Players", v.number_of_players_text || "-"],
        ["Accessory", L.subplatforms.find((s) => String(s.subplatform_id) === String(v.sub_platform_id))?.subplatform_name || "none"],
      ] : [
        ["Format", typeInfo(L, v.product_type_id).product_type_name],
        ["Running time", v.running_time || "-"],
        ["Studio", v.studio || "-"],
        ["Rating", ratingName(L, v.rating_id) || "-"],
        ["Starring", (v.starring || []).join(", ") || "-"],
        ["Directors", (v.directors || []).join(", ") || "-"],
      ]} />
      <QuickEdit key={`${pid}:${d.dataUpdatedAt}`} pid={pid} v={v} L={L} onFull={onFull} />
    </Paper>
  );
}

const quickSchema = z.object({
  long_name: z.string().trim().min(1, "A title needs a name"),
  running_time: z.string().regex(/^(\d{1,2}:\d{2})?$/, "Hours and minutes, like 1:45"),
});

type QuickValues = {
  long_name: string; sort_name: string; release_date: string | null; product_type_id: string; rating_id: string | null;
  sub_platform_id: string | null; number_of_players_text: string; running_time: string; studio: string; starring: string[]; directors: string[]; genres: string[];
};

function QuickEdit({ pid, v, L, onFull }: { pid: number; v: TitleDetail["value"]; L: Lookups; onFull: () => void }) {
  const can = useCan()("change");
  const qc = useQueryClient();
  const game = isGameType(L, v.product_type_id);
  const fam = game ? 2 : 1;
  const form = useForm<QuickValues>({
    initialValues: {
      long_name: v.long_name || "", sort_name: v.sort_name || "", release_date: toIsoDate(fromRedboxDate(v.release_date)),
      product_type_id: String(v.product_type_id ?? ""), rating_id: v.rating_id != null ? String(v.rating_id) : null,
      sub_platform_id: v.sub_platform_id != null ? String(v.sub_platform_id) : null, number_of_players_text: v.number_of_players_text || "",
      running_time: v.running_time || "", studio: v.studio || "", starring: v.starring || [], directors: v.directors || [],
      genres: Object.keys(v.genres || {}).filter((k) => v.genres?.[k]),
    },
    validate: zodResolver(quickSchema),
  });
  const save = useMutation({
    mutationFn: (fields: Record<string, unknown>) => api("/catalog/titles", { body: { product_id: pid, fields } }),
    onSuccess: () => { notifyOk("Saved. Publish the catalog to send it to kiosks."); qc.invalidateQueries({ queryKey: ["catalog"] }); },
  });
  const submit = form.onSubmit((vals) => {
    const fields: Record<string, unknown> = {};
    for (const k of Object.keys(vals) as (keyof QuickValues)[]) {
      if (!form.isDirty(k)) continue;
      const x = vals[k];
      if (k === "product_type_id" || k === "rating_id") fields[k] = x == null ? null : Number(x);
      else if (k === "sub_platform_id") fields[k] = x ? Number(x) : null;
      else if (k === "genres") fields[k] = (x as string[]).map(Number);
      else fields[k] = x;
    }
    save.mutate(fields);
  });
  const types = L.types.filter((t) => Number(t.product_family_id) === fam || (!game && Number(t.product_family_id) === 3))
    .map((t) => ({ value: String(t.product_type_id), label: t.product_type_name }));
  const ratings = L.ratings.filter((r) => Number(r.product_family_id) === fam).map((r) => ({ value: String(r.rating_id), label: r.name }));
  const genres = familyGenres(L, fam, form.values.genres);
  const ro = !can;
  return (
    <form onSubmit={submit}>
      <Stack gap="sm" mt="md">
        <TextInput label="Title" disabled={ro} {...form.getInputProps("long_name")} />
        <TextInput label="Sort name" disabled={ro} {...form.getInputProps("sort_name")} />
        <DateField label="Release date" disabled={ro} value={form.values.release_date} onChange={(x) => form.setFieldValue("release_date", x)} />
        <SimpleGrid cols={2} spacing="sm">
          <Select label={game ? "Platform" : "Format"} disabled={ro} data={types} allowDeselect={false} {...form.getInputProps("product_type_id")} />
          <Select label={game ? "ESRB rating" : "Rating"} disabled={ro} data={ratings} {...form.getInputProps("rating_id")} />
        </SimpleGrid>
        {game ? (
          <SimpleGrid cols={2} spacing="sm">
            <Select label="Accessory" placeholder="none" clearable disabled={ro} {...form.getInputProps("sub_platform_id")}
              data={L.subplatforms.map((s) => ({ value: String(s.subplatform_id), label: s.subplatform_name }))} />
            <TextInput label="Players" placeholder="e.g. 1-4 players" disabled={ro} {...form.getInputProps("number_of_players_text")} />
          </SimpleGrid>
        ) : (
          <>
            <TextInput label="Running time" placeholder="h:mm" disabled={ro} {...form.getInputProps("running_time")} />
            <TagsInput label="Starring" placeholder="Add a name, press Enter" disabled={ro} {...form.getInputProps("starring")} />
            <TagsInput label="Directors" placeholder="Add a name, press Enter" disabled={ro} {...form.getInputProps("directors")} />
          </>
        )}
        <TextInput label={game ? "Publisher" : "Studio"} disabled={ro} {...form.getInputProps("studio")} />
        <Checkbox.Group label={game ? "Game genres" : "Genres"} value={form.values.genres} onChange={(x) => form.setFieldValue("genres", x)}>
          <SimpleGrid cols={2} spacing={6} mt={6}>
            {genres.map((g) => <Checkbox key={g.genre_id} value={String(g.genre_id)} label={g.genre_name} disabled={ro} size="xs" />)}
          </SimpleGrid>
        </Checkbox.Group>
        <Group justify="space-between" mt="xs">
          <Button variant="default" leftSection={<IconEdit size={16} />} onClick={onFull}>Full editor</Button>
          {can && <Button type="submit" leftSection={<IconCheck size={16} />} disabled={!form.isDirty()} loading={save.isPending}>Save</Button>}
        </Group>
      </Stack>
    </form>
  );
}
