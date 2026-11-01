import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Anchor, Badge, Box, Button, Code, Grid, Group, NavLink, Paper, Progress, Select, SimpleGrid, Skeleton, Stack, Text, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { IconArrowBackUp, IconKey, IconRocket, IconSearch, IconUpload } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { ago, dateTime, num, short } from "@/lib/format";
import { EmptyState, ErrorState, LocalTable } from "@/components/DataTable";
import { PageHeader, Section, Status, UrlSearch, useUrlParam } from "@/components/ui";
import { PublishDialog, RollbackDialog, RolloutDialog, type TargetPreset } from "./dialogs";
import { targetText, type Labels, type ReleaseRepo, type RepoStatus, type StoreState } from "./types";

export function ReleasesPage() {
  const can = useCan();
  const { repo } = useParams();
  const [publish, setPublish] = useState(false);
  const [filter, setFilter] = useState("");
  const q = useQuery({ queryKey: ["releases", "overview"], queryFn: () => api<{ repositories: ReleaseRepo[] }>("/releases") });
  const repos = q.data?.repositories || [];
  const name = repo || repos[0]?.name;
  const shown = repos.filter((r) => !filter || r.name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <>
      <PageHeader stats="releases" title="Releases" description="Builds are published unreleased. Nothing reaches a kiosk until you roll it out."
        actions={<>
          {can("admin") && <Button component={Link} to="/access/tokens" variant="default" leftSection={<IconKey size={16} />}>CI tokens</Button>}
          {can() && <Button leftSection={<IconUpload size={16} />} onClick={() => setPublish(true)}>Publish a release</Button>}
        </>} />
      {q.isError ? <Paper bg="dark.7"><ErrorState error={q.error} onRetry={() => q.refetch()} /></Paper> : (
        <Grid gutter="md" align="flex-start">
          <Grid.Col span={{ base: 12, md: 4, lg: 3 }}>
            <Paper bg="dark.7" p={0} style={{ overflow: "hidden" }}>
              <Box p="sm" style={{ borderBottom: "1px solid var(--rb-line)" }}>
                <TextInput size="xs" placeholder="Filter repositories" leftSection={<IconSearch size={14} />} value={filter}
                  onChange={(e) => setFilter(e.currentTarget.value)} aria-label="Filter repositories" />
              </Box>
              {q.isPending ? (
                <Stack gap={10} p="md">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} h={22} />)}</Stack>
              ) : !repos.length ? (
                <EmptyState title="No repositories yet">Import a seed zip (System administration) or publish a release to create one.</EmptyState>
              ) : (
                <Box style={{ maxHeight: "calc(100vh - 230px)", overflow: "auto" }}>
                  {shown.map((r) => {
                    const sel = r.name === name;
                    return (
                      <NavLink key={r.name} component={Link} to={`/releases/${encodeURIComponent(r.name)}`} active={sel}
                        aria-current={sel ? "page" : undefined}
                        label={<Text size="sm" fw={sel ? 800 : 600} c={sel ? "#fff" : undefined} truncate>{r.name}</Text>}
                        rightSection={r.unreleased
                          ? <Badge size="sm" color="red" variant={sel ? "white" : "filled"}>{r.unreleased} new</Badge>
                          : <Text size="xs" fw={600} c={sel ? "#fff" : "dark.1"} truncate maw={110}>{r.served_label || short(r.served)}</Text>}
                        style={{
                          borderBottom: "1px solid var(--rb-line)",
                          ...(sel ? { background: "var(--rb-sel)", color: "#fff", boxShadow: "inset 3px 0 0 var(--rb-red)" } : {}),
                        }} />
                    );
                  })}
                  {!shown.length && <Text size="sm" p="md">No repository matches "{filter}".</Text>}
                </Box>
              )}
            </Paper>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 8, lg: 9 }}>
            {q.isPending ? <Skeleton h={420} /> : name ? <RepoDetail name={name} /> : null}
          </Grid.Col>
        </Grid>
      )}
      <PublishDialog opened={publish} onClose={() => setPublish(false)} />
    </>
  );
}

function RevName({ h, labels }: { h: string | null | undefined; labels: Labels }) {
  if (!h) return <Text span size="sm" c="dark.1">-</Text>;
  const l = labels[h];
  const extra = l && (l.label || l.version);
  return (
    <Group gap={6} wrap="nowrap" display="inline-flex">
      <Code title={h}>{short(h)}</Code>
      {extra && <Text span size="sm" fw={600} truncate maw={240}>{extra}</Text>}
    </Group>
  );
}

const stateKind: Record<StoreState, "ok" | "warn" | "bad" | "off"> = { current: "ok", behind: "warn", ahead: "bad", "unknown head": "bad", "no report": "off" };

function RepoDetail({ name }: { name: string }) {
  const can = useCan();
  const [limit, setLimit] = useState(40);
  const q = useQuery({
    queryKey: ["releases", "repo", name, limit],
    queryFn: () => api<RepoStatus>(`/releases/repo/${encodeURIComponent(name)}?limit=${limit}`),
  });
  const [rollout, setRollout] = useState<{ hash?: string | null; preset?: TargetPreset } | null>(null);
  const [rollback, setRollback] = useState<TargetPreset | null>(null);
  const [stateFilter, setStateFilter] = useUrlParam("state");
  const [storeQ] = useUrlParam("store");

  const st = q.data;
  const labels = useMemo(() => {
    const L: Labels = { ...(st?.labels || {}) };
    for (const r of st?.revisions || []) L[r.hash] = L[r.hash] || { label: r.label, version: r.version };
    return L;
  }, [st]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of st?.stores || []) c[s.state] = (c[s.state] || 0) + 1;
    return c;
  }, [st]);
  const stores = useMemo(() => (st?.stores || []).filter((s) =>
    (!stateFilter || s.state === stateFilter) &&
    (!storeQ || s.store.includes(storeQ) || (s.market || "").toLowerCase().includes(storeQ.toLowerCase()))), [st, stateFilter, storeQ]);

  if (q.isError) return <Paper bg="dark.7"><ErrorState error={q.error} onRetry={() => q.refetch()} /></Paper>;
  if (!st) return <Stack gap="md"><Skeleton h={190} /><Skeleton h={260} /><Skeleton h={260} /></Stack>;

  const byKind = (k: string) => st.rollouts.filter((r) => r.target_type === k);
  const newest = st.revisions[0];
  const total = st.stores.length;
  const current = counts.current || 0;
  const rolloutList = (k: string) => {
    const l = byKind(k);
    return l.length ? <Stack gap={4}>{l.map((r) => <Group key={r.target} gap={6} wrap="nowrap"><Text span size="sm" fw={700}>{r.target}</Text><Text span size="sm">→</Text><RevName h={r.head} labels={labels} /></Group>)}</Stack>
      : <Text span size="sm" c="dark.1">None</Text>;
  };

  return (
    <Stack gap="md">
      <Section title={<Text fw={800} fz="lg">{name}</Text>} actions={can() && <>
        <Button size="xs" variant="default" leftSection={<IconArrowBackUp size={15} />} onClick={() => setRollback({ kind: "all" })}>Roll back...</Button>
        {newest && <Button size="xs" leftSection={<IconRocket size={15} />} onClick={() => setRollout({ hash: newest.hash })}>
          Roll out {newest.version || newest.label || short(newest.hash)}
        </Button>}
      </>}>
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            {([["Everyone gets", <RevName h={st.default_head} labels={labels} />], ["Market rollouts", rolloutList("market")], ["Store rollouts", rolloutList("store")]] as const).map(([l, v]) => (
              <Stack key={l} gap={4}>
                <Text size="xs" fw={700} tt="uppercase" style={{ letterSpacing: ".06em" }} c="dark.1">{l}</Text>
                <Box fw={600}>{v}</Box>
              </Stack>
            ))}
          </SimpleGrid>
          {total > 0 && (
            <div>
              <Group justify="space-between" mb={6}>
                <Text size="sm" fw={700}>Stores up to date</Text>
                <Text size="sm" fw={600}>
                  {num(current)} of {num(total)}
                  {(["behind", "ahead", "unknown head", "no report"] as const).map((s) => counts[s] ? ` · ${num(counts[s])} ${s}` : "").join("")}
                </Text>
              </Group>
              <Progress.Root size="lg" aria-label="Stores up to date">
                <Progress.Section value={(current / total) * 100} color="teal" />
                <Progress.Section value={((counts.behind || 0) / total) * 100} color="yellow" />
                <Progress.Section value={(((counts.ahead || 0) + (counts["unknown head"] || 0)) / total) * 100} color="red" />
              </Progress.Root>
            </div>
          )}
          <Text size="sm" c="dark.1">
            A store gets its own rollout, else its market's, else the default (the pin, else the newest released revision). updatemgrd never
            moves back: a store past what it is served keeps its version until a revert reaches it.
          </Text>
        </Stack>
      </Section>

      <Section title="Revisions" p={0} actions={st.revisions.length >= limit && (
        <Button size="xs" variant="default" onClick={() => setLimit((l) => l + 60)}>Show older</Button>
      )}>
        <LocalTable rows={st.revisions} rowKey={(r) => r.hash} maxHeight={420} initialSort={{ sort: "seq", dir: "desc" }}
          empty={<EmptyState title="No revisions">Publish a release to add one.</EmptyState>}
          columns={[
            { id: "seq", header: "Seq", cell: (r) => r.seq, value: (r) => r.seq, align: "right", width: 70 },
            { id: "hash", header: "Hash", cell: (r) => <Code title={r.hash}>{short(r.hash)}</Code>, value: (r) => r.hash },
            { id: "version", header: "Version", cell: (r) => r.version, value: (r) => r.version },
            { id: "label", header: "Label", cell: (r) => r.label, value: (r) => r.label, width: 260 },
            { id: "released", header: "State", cell: (r) => r.released ? <Status kind="ok">released</Status> : <Status kind="warn">unreleased</Status>, value: (r) => (r.released ? 1 : 0) },
            { id: "origin", header: "Origin", cell: (r) => r.origin, value: (r) => r.origin },
            { id: "created_at", header: "Added", cell: (r) => dateTime(r.created_at), value: (r) => r.created_at },
            ...(can() ? [{ id: "act", header: "", cell: (r: RepoStatus["revisions"][number]) => <Button size="compact-xs" variant="default" onClick={() => setRollout({ hash: r.hash })}>Roll out...</Button> }] : []),
          ]} />
      </Section>

      <Section title="Store and market rollouts" p={0}>
        <LocalTable rows={st.rollouts} rowKey={(r) => `${r.target_type}:${r.target}`}
          empty={<EmptyState title="No rollouts">Every store gets the default.</EmptyState>}
          columns={[
            { id: "target", header: "Target", cell: (r) => targetText(r.target_type, r.target), value: (r) => `${r.target_type} ${r.target}` },
            { id: "head", header: "Served", cell: (r) => <RevName h={r.head} labels={labels} />, value: (r) => labels[r.head]?.seq ?? r.head },
            { id: "created_at", header: "Since", cell: (r) => dateTime(r.created_at), value: (r) => r.created_at },
            ...(can() ? [{ id: "act", header: "", cell: (r: RepoStatus["rollouts"][number]) => (
              <Button size="compact-xs" variant="default" leftSection={<IconArrowBackUp size={13} />}
                onClick={() => setRollback({ kind: r.target_type, store: r.target_type === "store" ? r.target : null, market: r.target_type === "market" ? r.target : null })}>
                Roll back...
              </Button>) }] : []),
          ]} />
      </Section>

      <Section title="Stores" p={0}>
        <Box px="md" pt="sm">
          <Text size="sm" c="dark.1" mb="sm">What each store last reported (<Code>u p</Code>, every 15 minutes) against what it is served now.</Text>
          <Group gap="sm" mb="sm" wrap="wrap">
            <UrlSearch param="store" placeholder="Search store or market" />
            <Select w={200} clearable placeholder="Any state" value={stateFilter} onChange={setStateFilter} aria-label="Filter by state"
              data={(["current", "behind", "ahead", "unknown head", "no report"] as const).map((s) => ({ value: s, label: `${s} (${num(counts[s] || 0)})` }))} />
          </Group>
        </Box>
        <LocalTable rows={stores} rowKey={(r) => r.store} maxHeight={520}
          empty={<EmptyState title={total ? "No store matches" : "No store has polled yet"}>{total ? "Clear the search or state filter." : "Stores show up after their first u p poll."}</EmptyState>}
          columns={[
            { id: "store", header: "Store", cell: (r) => <Anchor component={Link} to={`/kiosks/${encodeURIComponent(r.store)}`} fw={700}>{r.store}</Anchor>, value: (r) => Number(r.store) || r.store },
            { id: "market", header: "Market", cell: (r) => r.market, value: (r) => r.market },
            { id: "state", header: "State", cell: (r) => <Status kind={stateKind[r.state] || "off"}>{r.state}</Status>, value: (r) => r.state },
            { id: "reported", header: "Runs", cell: (r) => <RevName h={r.reported} labels={labels} />, value: (r) => (r.reported && labels[r.reported]?.seq) ?? r.reported },
            { id: "reported_at", header: "Reported", cell: (r) => <span title={dateTime(r.reported_at)}>{ago(r.reported_at)}</span>, value: (r) => r.reported_at },
            { id: "served", header: "Served", cell: (r) => <RevName h={r.served} labels={labels} />, value: (r) => (r.served && labels[r.served]?.seq) ?? r.served },
            ...(can() ? [{ id: "act", header: "", cell: (r: RepoStatus["stores"][number]) => (
              <Group gap={6} wrap="nowrap">
                <Button size="compact-xs" variant="default" onClick={() => setRollout({ hash: newest?.hash, preset: { kind: "store", store: r.store } })}>Roll out...</Button>
                <Button size="compact-xs" variant="default" onClick={() => setRollback({ kind: "store", store: r.store })}>Roll back...</Button>
              </Group>) }] : []),
          ]} />
      </Section>

      <Section title="History" p={0}>
        <LocalTable rows={st.log} rowKey={(r) => r.id} initialSort={{ sort: "created_at", dir: "desc" }}
          empty={<EmptyState title="No rollouts yet">Rollouts and rollbacks of this repository are listed here.</EmptyState>}
          columns={[
            { id: "created_at", header: "When", cell: (r) => dateTime(r.created_at), value: (r) => r.created_at },
            { id: "action", header: "Action", cell: (r) => <Text span size="sm" fw={700}>{r.action}</Text>, value: (r) => r.action },
            { id: "target", header: "Target", cell: (r) => targetText(r.target_type, r.target), value: (r) => `${r.target_type} ${r.target}` },
            { id: "previous", header: "Before", cell: (r) => r.previous ? <RevName h={r.previous} labels={labels} /> : "the default" },
            { id: "head", header: "After", cell: (r) => r.head ? <RevName h={r.head} labels={labels} /> : "the default" },
            { id: "actor", header: "By", cell: (r) => r.actor, value: (r) => r.actor },
          ]} />
      </Section>

      {rollout && <RolloutDialog name={name} st={st} hash={rollout.hash} preset={rollout.preset} opened onClose={() => setRollout(null)} />}
      {rollback && <RollbackDialog name={name} st={st} preset={rollback} opened onClose={() => setRollback(null)} />}
    </Stack>
  );
}
