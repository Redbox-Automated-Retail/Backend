import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useInfiniteQuery, keepPreviousData, type QueryKey } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Box, Button, Group, Paper, Progress, Skeleton, Stack, Text } from "@mantine/core";
import { IconArrowsSort, IconSortAscending, IconSortDescending, IconAlertTriangle, IconInbox } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";
import type { Page } from "@/lib/api";
import { num } from "@/lib/format";

export type Column<T> = {
  id: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** server sort key; columns without it can't be sorted */
  sort?: string;
  width?: number | string;
  align?: "left" | "right";
};

export type Sort = { sort?: string; dir: "asc" | "desc" };

/** Sort state kept in the URL (?sort=&dir=) so a sorted view can be linked and survives reloads. */
export function useUrlSort(def: Sort, prefix = ""): [Sort, (s: Sort) => void] {
  const [sp, setSp] = useSearchParams();
  const s: Sort = { sort: sp.get(prefix + "sort") || def.sort, dir: (sp.get(prefix + "dir") as "asc" | "desc") || def.dir };
  const set = (n: Sort) => setSp((p) => {
    const q = new URLSearchParams(p);
    if (n.sort) q.set(prefix + "sort", n.sort); else q.delete(prefix + "sort");
    q.set(prefix + "dir", n.dir);
    return q;
  }, { replace: true });
  return [s, set];
}

function SortIcon({ active, dir }: { active: boolean; dir: "asc" | "desc" }) {
  if (!active) return <IconArrowsSort size={14} style={{ opacity: 0.55, verticalAlign: -2 }} />;
  return dir === "asc" ? <IconSortAscending size={14} style={{ verticalAlign: -2 }} /> : <IconSortDescending size={14} style={{ verticalAlign: -2 }} />;
}

function Header<T>({ columns, sort, onSort }: { columns: Column<T>[]; sort?: Sort; onSort?: (s: Sort) => void }) {
  return (
    <thead>
      <tr>
        {columns.map((c) => {
          const sortable = !!(c.sort && onSort);
          const active = sortable && sort?.sort === c.sort;
          const next = () => onSort!({ sort: c.sort, dir: active && sort!.dir === "asc" ? "desc" : "asc" });
          return (
            <th key={c.id} className={sortable ? "sortable" : ""} style={{ width: c.width, textAlign: c.align || "left" }}
              onClick={sortable ? next : undefined} tabIndex={sortable ? 0 : undefined}
              onKeyDown={sortable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); next(); } } : undefined}
              aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}>
              {c.header} {sortable && <SortIcon active={!!active} dir={sort!.dir} />}
            </th>
          );
        })}
      </tr>
    </thead>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Stack align="center" gap={6} py={48} px="md">
      <IconInbox size={34} stroke={1.5} />
      <Text fw={700}>{title}</Text>
      {children && <Text size="sm" ta="center" maw={520}>{children}</Text>}
    </Stack>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <Stack align="center" gap={8} py={40} px="md">
      <IconAlertTriangle size={32} color="var(--mantine-color-red-5)" />
      <Text fw={700}>Couldn't load this</Text>
      <Text size="sm" ta="center" maw={560}>{error instanceof Error ? error.message : String(error)}</Text>
      {onRetry && <Button variant="default" size="xs" onClick={onRetry}>Try again</Button>}
    </Stack>
  );
}

function SkeletonRows({ cols, rows = 10 }: { cols: number; rows?: number }) {
  return (
    <tbody>
      {Array.from({ length: rows }, (_, i) => (
        <tr key={i}>{Array.from({ length: cols }, (_, j) => <td key={j}><Skeleton h={14} radius="sm" width={`${55 + ((i * 7 + j * 13) % 40)}%`} /></td>)}</tr>
      ))}
    </tbody>
  );
}

const ROW_H = 41;

/**
 * A table over a server-side list: sorted and filtered by the server, fetched 100 rows at a time as you scroll,
 * rendered virtually so 76,000 rows stay smooth.
 */
export function ServerTable<T>({ queryKey, fetchPage, columns, sort, onSort, rowKey, onRowClick, empty, height = "calc(100vh - 290px)", pageSize = 100, summary }: {
  queryKey: QueryKey;
  fetchPage: (p: { limit: number; offset: number; sort?: string; dir: "asc" | "desc" }) => Promise<Page<T>>;
  columns: Column<T>[];
  sort?: Sort;
  onSort?: (s: Sort) => void;
  rowKey: (row: T) => string | number;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  height?: string | number;
  pageSize?: number;
  summary?: (total: number, loaded: number) => ReactNode;
}) {
  const q = useInfiniteQuery({
    queryKey: [...queryKey, sort?.sort, sort?.dir],
    queryFn: ({ pageParam }) => fetchPage({ limit: pageSize, offset: pageParam, sort: sort?.sort, dir: sort?.dir || "asc" }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((a, p) => a + p.rows.length, 0);
      return loaded < last.total && last.rows.length ? loaded : undefined;
    },
    placeholderData: keepPreviousData,
  });
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.rows) ?? [], [q.data]);
  const total = q.data?.pages[0]?.total ?? 0;
  const scrollRef = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => scrollRef.current, estimateSize: () => ROW_H, overscan: 12 });
  const items = v.getVirtualItems();
  const last = items[items.length - 1];
  useEffect(() => {
    if (last && last.index >= rows.length - 30 && q.hasNextPage && !q.isFetchingNextPage) q.fetchNextPage();
  }, [last?.index, rows.length, q.hasNextPage, q.isFetchingNextPage, q.fetchNextPage]);
  // a new sort or filter starts at the top
  useEffect(() => { scrollRef.current?.scrollTo({ top: 0 }); }, [JSON.stringify(queryKey), sort?.sort, sort?.dir]);

  const refreshing = q.isFetching && !q.isFetchingNextPage && !q.isPending;
  const padTop = items.length ? items[0].start : 0;
  const padBottom = items.length ? v.getTotalSize() - items[items.length - 1].end : 0;
  return (
    <Paper p={0} style={{ overflow: "hidden" }} bg="dark.7">
      <Group justify="space-between" px="md" py={8} style={{ borderBottom: "1px solid var(--rb-line)" }} mih={38}>
        <Text size="sm" fw={600}>
          {q.isPending ? "Loading..." : q.isError ? " " : summary ? summary(total, rows.length) : `${num(total)} ${total === 1 ? "row" : "rows"}`}
        </Text>
        {q.isFetchingNextPage && <Text size="xs" fw={600}>Loading more...</Text>}
      </Group>
      <Box h={3}>{refreshing && <Progress value={100} animated striped size={3} radius={0} aria-label="Refreshing" />}</Box>
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <div ref={scrollRef} style={{ height, overflow: "auto", opacity: refreshing ? 0.6 : 1, transition: "opacity .15s" }}>
          <table className="rb-table" aria-busy={q.isFetching}>
            <Header columns={columns} sort={sort} onSort={onSort} />
            {q.isPending ? <SkeletonRows cols={columns.length} /> : (
              <tbody>
                {padTop > 0 && <tr style={{ height: padTop }}><td colSpan={columns.length} style={{ padding: 0, border: 0 }} /></tr>}
                {items.map((it) => {
                  const r = rows[it.index];
                  return (
                    <tr key={rowKey(r)} className={onRowClick ? "clickable" : ""} style={{ height: ROW_H }}
                      onClick={onRowClick ? (e) => { if (!(e.target as HTMLElement).closest("button,a,input,label")) onRowClick(r); } : undefined}
                      tabIndex={onRowClick ? 0 : undefined} onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}>
                      {columns.map((c) => <td key={c.id} className={c.align === "right" ? "rb-num" : ""} style={{ maxWidth: c.width }}>{c.cell(r)}</td>)}
                    </tr>
                  );
                })}
                {padBottom > 0 && <tr style={{ height: padBottom }}><td colSpan={columns.length} style={{ padding: 0, border: 0 }} /></tr>}
              </tbody>
            )}
          </table>
          {!q.isPending && !rows.length && (empty ?? <EmptyState title="Nothing here" />)}
        </div>
      )}
    </Paper>
  );
}

/** The same table over rows already in hand (small lists), sorted in the browser. */
export function LocalTable<T>({ rows, columns, rowKey, onRowClick, empty, loading, error, onRetry, maxHeight, sortable = true, initialSort }: {
  rows: T[] | undefined;
  columns: (Column<T> & { value?: (row: T) => string | number | null | undefined })[];
  rowKey: (row: T, i: number) => string | number;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  maxHeight?: number | string;
  sortable?: boolean;
  initialSort?: Sort;
}) {
  const [sp, setSp] = useState<Sort>(initialSort || { dir: "asc" });
  const sorted = useMemo(() => {
    if (!rows) return [];
    const col = columns.find((c) => (c.sort || c.id) === sp.sort);
    if (!col || !col.value) return rows;
    const val = col.value;
    return [...rows].sort((a, b) => {
      const x = val(a), y = val(b);
      const r = x == null ? 1 : y == null ? -1 : typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
      return sp.dir === "asc" ? r : -r;
    });
  }, [rows, sp.sort, sp.dir, columns]);
  const cols = columns.map((c) => ({ ...c, sort: sortable && c.value ? c.sort || c.id : undefined }));
  return (
    <Paper p={0} style={{ overflow: "hidden" }} bg="dark.7">
      {error ? <ErrorState error={error} onRetry={onRetry} /> : (
        <div style={{ maxHeight, overflow: "auto" }}>
          <table className="rb-table">
            <Header columns={cols} sort={sp} onSort={sortable ? setSp : undefined} />
            {loading ? <SkeletonRows cols={cols.length} rows={6} /> : (
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={rowKey(r, i)} className={onRowClick ? "clickable" : ""} tabIndex={onRowClick ? 0 : undefined}
                    onClick={onRowClick ? (e) => { if (!(e.target as HTMLElement).closest("button,a,input,label")) onRowClick(r); } : undefined}
                    onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}>
                    {cols.map((c) => <td key={c.id} className={c.align === "right" ? "rb-num" : ""} style={{ maxWidth: c.width, whiteSpace: c.width ? "normal" : undefined }}>{c.cell(r)}</td>)}
                  </tr>
                ))}
              </tbody>
            )}
          </table>
          {!loading && !sorted.length && (empty ?? <EmptyState title="Nothing here" />)}
        </div>
      )}
    </Paper>
  );
}
