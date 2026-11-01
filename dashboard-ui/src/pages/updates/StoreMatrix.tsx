import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Box, Group, Menu, MultiSelect, Pagination, Progress, Select, Skeleton, Stack, Text, UnstyledButton } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconArrowsSort, IconCheck, IconSortAscending, IconSortDescending } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { ago, dateTime, num } from "@/lib/format";
import { notifyOk } from "@/lib/notify";
import { EmptyState, ErrorState, useUrlSort } from "@/components/DataTable";
import { Section, UrlSearch, useUrlParam } from "@/components/ui";

type CellState = "current" | "behind" | "missing" | "not subscribed";
type LinkMode = "linked" | "unlinked" | "auto";
type Cell = { state: CellState; reported: string | null; link: LinkMode };
type StoreRow = { store: { number: string; last_check_in: string | null }; repos: Record<string, Cell> };
type Matrix = { repositories: string[]; stores: StoreRow[] };

const PAGE = 50;
const color: Record<CellState, string> = { current: "teal", behind: "yellow", missing: "red", "not subscribed": "gray" };
const stateRank: Record<CellState, number> = { missing: 0, behind: 1, current: 2, "not subscribed": 3 };
const modes: { mode: LinkMode; label: string; hint: string }[] = [
  { mode: "linked", label: "Link", hint: "always offer it to this store" },
  { mode: "unlinked", label: "Unlink", hint: "never offer it to this store" },
  { mode: "auto", label: "Automatic", hint: "follow auto-subscribe" },
];

export function StoreMatrix() {
  const can = useCan();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["updates", "stores"], queryFn: () => api<Matrix>("/updates/stores") });
  const [search] = useUrlParam("mq");
  const [stateF, setStateF] = useUrlParam("mstate");
  const [cols, setCols] = useUrlParam("mrepos");
  const [pageS, setPage] = useUrlParam("mpage");
  const [sort, setSort] = useUrlSort({ sort: "number", dir: "asc" }, "m");
  const link = useMutation({
    mutationFn: (b: { number: string; repo: string; mode: LinkMode }) => api("/updates/links", { body: b }),
    onSuccess: () => { notifyOk("Saved; applies on the store's next poll"); qc.invalidateQueries({ queryKey: ["updates"] }); },
  });

  const repos = q.data?.repositories || [];
  const shownRepos = cols ? cols.split(",").filter((r) => repos.includes(r)) : repos;
  const rows = useMemo(() => {
    let r = q.data?.stores || [];
    if (search) r = r.filter((s) => s.store.number.includes(search));
    if (stateF) r = r.filter((s) => shownRepos.some((n) => s.repos[n]?.state === stateF));
    const dir = sort.dir === "asc" ? 1 : -1;
    const key = sort.sort || "number";
    return [...r].sort((a, b) => {
      let x: number | string, y: number | string;
      if (key === "number") { x = Number(a.store.number) || 0; y = Number(b.store.number) || 0; if (x === y) { x = a.store.number; y = b.store.number; } }
      else if (key === "check_in") { x = a.store.last_check_in || ""; y = b.store.last_check_in || ""; }
      else { x = stateRank[a.repos[key]?.state] ?? 9; y = stateRank[b.repos[key]?.state] ?? 9; }
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }, [q.data, search, stateF, sort.sort, sort.dir, cols]); // eslint-disable-line react-hooks/exhaustive-deps
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const page = Math.min(pages, Math.max(1, Number(pageS) || 1));
  const slice = rows.slice((page - 1) * PAGE, page * PAGE);

  const head = (key: string, label: string, sticky = false) => {
    const active = (sort.sort || "number") === key;
    const next = () => { setSort({ sort: key, dir: active && sort.dir === "asc" ? "desc" : "asc" }); setPage(null); };
    return (
      <th key={key} className="sortable" tabIndex={0} onClick={next} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); next(); } }}
        style={sticky ? { position: "sticky", left: 0, zIndex: 2, background: "var(--mantine-color-dark-7)" } : undefined}>
        {label}{" "}
        {!active ? <IconArrowsSort size={14} style={{ opacity: 0.55, verticalAlign: -2 }} />
          : sort.dir === "asc" ? <IconSortAscending size={14} style={{ verticalAlign: -2 }} /> : <IconSortDescending size={14} style={{ verticalAlign: -2 }} />}
      </th>
    );
  };

  return (
    <Section title="Stores and repository assignment" p={0}>
      <Stack gap="sm" px="md" pt="sm" pb="sm">
        <Text size="sm" c="dark.1">
          Click a cell to link, unlink, or reset it to automatic. "auto" means every auto-subscribed repository unless the store has explicit links.
        </Text>
        <Group gap="sm" wrap="wrap" align="flex-end">
          <UrlSearch param="mq" placeholder="Find a store number" />
          <Select w={220} clearable placeholder="Any state" aria-label="Only stores with a repository in this state" value={stateF}
            onChange={(v) => { setStateF(v); setPage(null); }}
            data={(["missing", "behind", "current", "not subscribed"] as const).map((s) => ({ value: s, label: `has ${s}` }))} />
          <MultiSelect w={340} clearable searchable placeholder={cols ? undefined : "All repositories"} aria-label="Repositories to show"
            data={repos} value={shownRepos.length === repos.length ? [] : shownRepos} onChange={(v) => setCols(v.join(","))} maxDropdownHeight={300} />
        </Group>
      </Stack>
      <Box h={3}>{q.isFetching && !q.isPending && <Progress value={100} animated striped size={3} radius={0} aria-label="Refreshing" />}</Box>
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.isPending ? (
        <Stack gap={8} p="md">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} h={26} />)}</Stack>
      ) : !q.data.stores.length ? (
        <EmptyState title="No store has polled yet">Stores appear here after their first updatemgrd poll (u p).</EmptyState>
      ) : (
        <>
          <div style={{ overflow: "auto", maxHeight: "70vh" }}>
            <table className="rb-table" style={{ width: "auto", minWidth: "100%" }}>
              <thead>
                <tr>
                  {head("number", "Store", true)}
                  {head("check_in", "Check-in")}
                  {shownRepos.map((r) => head(r, r))}
                </tr>
              </thead>
              <tbody>
                {slice.map((s) => (
                  <tr key={s.store.number}>
                    <td style={{ position: "sticky", left: 0, zIndex: 1, background: "var(--mantine-color-dark-7)" }}>
                      <Anchor component={Link} to={`/kiosks/${encodeURIComponent(s.store.number)}`} fw={700}>{s.store.number}</Anchor>
                    </td>
                    <td title={dateTime(s.store.last_check_in)}>{ago(s.store.last_check_in)}</td>
                    {shownRepos.map((r) => {
                      const c = s.repos[r];
                      if (!c) return <td key={r}>-</td>;
                      const badge = (
                        <Group gap={6} wrap="nowrap">
                          <Badge size="sm" variant="light" color={color[c.state]}>{c.state}</Badge>
                          <Text span size="xs" fw={600}>{c.link}</Text>
                        </Group>
                      );
                      return (
                        <td key={r} title={c.reported ? `reports ${c.reported}` : "no report"}>
                          {can() ? (
                            <Menu position="bottom-start" withinPortal shadow="md">
                              <Menu.Target>
                                <UnstyledButton aria-label={`${r} for store ${s.store.number}: ${c.state}, ${c.link}`}>{badge}</UnstyledButton>
                              </Menu.Target>
                              <Menu.Dropdown>
                                <Menu.Label>{r} for store {s.store.number}</Menu.Label>
                                {modes.map((m) => (
                                  <Menu.Item key={m.mode} disabled={link.isPending} leftSection={c.link === m.mode ? <IconCheck size={14} /> : <Box w={14} />}
                                    onClick={() => c.link !== m.mode && link.mutate({ number: s.store.number, repo: r, mode: m.mode })}>
                                    <Text size="sm" fw={600}>{m.label}</Text><Text size="xs" c="dark.1">{m.hint}</Text>
                                  </Menu.Item>
                                ))}
                              </Menu.Dropdown>
                            </Menu>
                          ) : badge}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {!rows.length && <EmptyState title="No store matches">Clear the search or the state filter.</EmptyState>}
          </div>
          <Group justify="space-between" px="md" py="sm" style={{ borderTop: "1px solid var(--rb-line)" }}>
            <Text size="sm" fw={600}>
              {rows.length ? `${num((page - 1) * PAGE + 1)}-${num(Math.min(page * PAGE, rows.length))} of ${num(rows.length)} stores` : "0 stores"}
              {" · "}{num(shownRepos.length)} of {num(repos.length)} repositories
            </Text>
            {pages > 1 && <Pagination size="sm" total={pages} value={page} onChange={(p) => setPage(p === 1 ? null : String(p))} />}
          </Group>
        </>
      )}
    </Section>
  );
}
