// Titles: a poster grid filtered by format / platform and what to show (new releases, coming soon, changed, no box art),
// loaded in pages. Clicking a poster opens the title editor, a modal (?edit=<product id> or ?edit=new).
import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, keepPreviousData } from "@tanstack/react-query";
import { ActionIcon, Anchor, Badge, Box, Button, Group, Paper, Select, Skeleton, Text, Tooltip } from "@mantine/core";
import { IconPlus, IconSortAscending, IconSortDescending } from "@tabler/icons-react";
import { api, qs } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { num } from "@/lib/format";
import { EmptyState, ErrorState } from "@/components/DataTable";
import { UrlSearch, useUrlParam } from "@/components/ui";
import { ArtBox, isGameType, ratingName, typeInfo, useLookups, type Lookups } from "./shared";
import { TitleEditor } from "./TitleEditor";
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

function filterParams(fmt: string | null, show: string | null): Record<string, string> {
  const p: Record<string, string> = {};
  if (fmt === "games") p.family = "2";
  else if (fmt && /^\d+$/.test(fmt)) p.types = fmt;
  if (show === "newest") p.newest = "true";
  else if (show === "soon") p.soon = "true";
  else if (show === "changed") p.changed = "true";
  else if (show === "noart") p.noart = "true";
  return p;
}
export function TitlesTab() {
  const L = useLookups();
  const can = useCan()("change");
  const [q] = useUrlParam("q");
  const [fmt, setFmt] = useUrlParam("format");
  const [show, setShow] = useUrlParam("show");
  const [sort, setSort] = useUrlParam("sort");
  const [dir, setDir] = useUrlParam("dir");
  const [edit, setEdit] = useUrlParam("edit");

  const params = { q: q || "", ...filterParams(fmt, show), sort: sort || "", dir: dir || "" };
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

  // the counts come with every page; the dropdowns list each format / platform with how many titles it has
  const formats = useMemo(() => {
    if (!first || !L.data) return [];
    const opt = (t: { product_type_id: number; n: number }) => ({ value: String(t.product_type_id), label: `${typeInfo(L.data, t.product_type_id).product_type_name} (${num(t.n)})` });
    const games = first.by_type.filter((t) => isGameType(L.data, t.product_type_id));
    const movies = first.by_type.filter((t) => !isGameType(L.data, t.product_type_id));
    return [
      { group: "Movies", items: movies.map(opt) },
      { group: "Games", items: [{ value: "games", label: `All games (${num(games.reduce((a, t) => a + Number(t.n), 0))})` }, ...games.map(opt)] },
    ];
  }, [first, L.data]);
  const shows = first ? [
    { value: "newest", label: "New releases first" }, { value: "soon", label: `Coming soon (${num(first.counts.soon)})` },
    { value: "changed", label: `Changed since the last build (${num(first.counts.changed)})` }, { value: "noart", label: `No box art (${num(first.counts.noart)})` },
  ] : [];
  const refreshing = browse.isFetching && !browse.isFetchingNextPage && !browse.isPending;
  const editPid = edit && edit !== "new" ? Number(edit) : null;

  return (
    <>
      <Group gap="sm" mb="md" wrap="wrap" align="flex-end">
        <UrlSearch placeholder="Title, product id or barcode" />
        <Select w={230} aria-label="Format or platform" placeholder="All formats" value={fmt} onChange={setFmt} clearable searchable
          data={formats} nothingFoundMessage="No format" />
        <Select w={260} aria-label="Show" placeholder="All titles" value={show} onChange={setShow} clearable data={shows} />
        <Select w={160} aria-label="Sort by" value={sort || ""} onChange={(v) => setSort(v || null)} allowDeselect={false}
          data={[{ value: "", label: "Default order" }, { value: "title", label: "Title" }, { value: "release", label: "Release date" }, { value: "id", label: "Product id" }]} />
        <Tooltip label={dir === "asc" ? "Ascending" : "Descending"}>
          <ActionIcon variant="default" size={36} aria-label="Sort direction" onClick={() => setDir(dir === "asc" ? "desc" : "asc")}>
            {dir === "asc" ? <IconSortAscending size={18} /> : <IconSortDescending size={18} />}
          </ActionIcon>
        </Tooltip>
        {can && <Button leftSection={<IconPlus size={16} />} onClick={() => setEdit("new")}>New title</Button>}
      </Group>
      <Group justify="space-between" mb="xs" mih={22}>
        <Text size="sm" fw={600}>{browse.isPending ? "Loading titles..." : browse.isError ? "" : `${num(total)} ${total === 1 ? "title" : "titles"}${rows.length < total ? ` · showing ${num(rows.length)}` : ""}`}</Text>
        {refreshing && <Text size="xs" fw={600}>Updating...</Text>}
      </Group>
      {browse.isError ? <Paper bg="dark.7"><ErrorState error={browse.error} onRetry={() => browse.refetch()} /></Paper> : (
        <Box style={{ opacity: refreshing ? 0.6 : 1, transition: "opacity .15s" }}>
          <div className="rb-posters" aria-busy={browse.isFetching}>
            {browse.isPending ? <PosterSkeletons n={24} /> : rows.map((r) => (
              <Poster key={r.product_id} r={r} L={L.data} selected={editPid === r.product_id} onClick={() => setEdit(String(r.product_id))} />
            ))}
            {browse.isFetchingNextPage && <PosterSkeletons n={8} />}
          </div>
          {!browse.isPending && !rows.length && (
            <Paper bg="dark.7" mt="sm">
              {first?.counts.total ? <EmptyState title="No title matches">Try another search or filter.</EmptyState> : (
                <EmptyState title="No catalog yet">Import one under <Anchor component={Link} to="/admin/catalog">System administration</Anchor> (catalog import), then edit and publish it here.</EmptyState>
              )}
            </Paper>
          )}
          <div ref={sentinel} />
          {browse.hasNextPage && !browse.isFetchingNextPage && (
            <Group justify="center" mt="md"><Button variant="default" onClick={() => browse.fetchNextPage()}>Show more ({num(total - rows.length)} left)</Button></Group>
          )}
        </Box>
      )}
      <TitleEditor pid={editPid} opened={!!edit} onClose={() => setEdit(null)}
        onCreated={(pid) => setEdit(String(pid))} onDeleted={() => setEdit(null)} />
    </>
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
