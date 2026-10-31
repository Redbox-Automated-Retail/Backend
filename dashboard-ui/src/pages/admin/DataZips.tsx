import { useEffect, useMemo, useState } from "react";
import { ActionIcon, Alert, Anchor, Badge, Box, Button, Checkbox, Code, Group, Progress, Select, SimpleGrid, Stack, Switch, Text, TextInput, Tooltip } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconDownload, IconHammer, IconPackageImport, IconPlus, IconTrash, IconX } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { bytes, dateTime, dayjs, num } from "@/lib/format";
import { confirm, notifyOk } from "@/lib/notify";
import { EmptyState, LocalTable } from "@/components/DataTable";
import { DateField } from "@/components/fields";
import { JobProgress, type Job } from "@/components/JobProgress";
import { Section, Status } from "@/components/ui";

type SeedFile = { file: string; size: number; date: string | null; edition: string | null; imported_at: string | null };
type Edition = { name: string; source: string; edition_date: string | null; heads: unknown; revisions: Record<string, number> | null; imported_at: string };
type Packages = { dir: string | null; files: SeedFile[]; editions: Edition[]; jobs: Job[] };
type Export = { file: string; size: number; modified: string };

const DVD = [
  { value: "latest", label: "dvd-data only from the newest selected seed" },
  { value: "none", label: "skip dvd-data" },
  { value: "all", label: "dvd-data from every seed" },
];

/** Update-store seeds: import from PACKAGES_DIR into editions, build new seed zips, follow the jobs. */
export function DataZips() {
  const can = useCan();
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const q = useQuery({
    queryKey: ["packages"],
    queryFn: () => api<Packages>("/packages"),
    refetchInterval: running ? 3000 : false,
    meta: { silent: running },
  });
  useEffect(() => { setRunning(!!q.data?.jobs.some((j) => j.status === "running")); }, [q.data]);
  // a job that just ended may have written a seed or an edition
  useEffect(() => { if (!running) qc.invalidateQueries({ queryKey: ["packages", "exports"] }); }, [running]); // eslint-disable-line react-hooks/exhaustive-deps
  const [activeJob, setActiveJob] = useState<{ id: number; what: string } | null>(null);
  const finished = (j: Job) => {
    notifyOk(j.status === "done" ? `Job ${j.id} finished` : `Job ${j.id} failed`, j.source);
    qc.invalidateQueries({ queryKey: ["packages"] });
    qc.invalidateQueries({ queryKey: ["updates"] });
    qc.invalidateQueries({ queryKey: ["releases"] });
  };

  return (
    <Stack gap="md">
      <Text size="sm" c="dark.1">
        Redbox update-store seeds (<Code>data-&lt;date&gt;.zip</Code>, the data file the store installer activates on a new kiosk) from{" "}
        <Code>{q.data ? q.data.dir || "(no PACKAGES_DIR mounted)" : "..."}</Code>, read-only. Each import becomes an edition; every repository is served from
        the newest edition that has it, with content of older editions kept. dvd-data is about 1 GB per seed.
      </Text>
      {activeJob && (
        <Section title={`${activeJob.what} (job ${activeJob.id})`} actions={
          <ActionIcon variant="subtle" color="gray" aria-label="Hide" onClick={() => setActiveJob(null)}><IconX size={16} /></ActionIcon>}>
          <JobProgress jobId={activeJob.id} onFinished={finished} />
        </Section>
      )}
      <Seeds data={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} canChange={can()}
        onStarted={(id) => { setActiveJob({ id, what: "Import" }); setRunning(true); }} />
      <Jobs jobs={q.data?.jobs} loading={q.isPending} />
      <Section title="Editions" p={0}>
        <LocalTable rows={q.data?.editions} loading={q.isPending} rowKey={(e) => e.name} maxHeight={360} initialSort={{ sort: "edition_date", dir: "desc" }}
          empty={<EmptyState title="No edition imported yet">Select seeds above and import them.</EmptyState>}
          columns={[
            { id: "name", header: "Edition", cell: (e) => <Text span size="sm" fw={700}>{e.name}</Text>, value: (e) => e.name },
            { id: "edition_date", header: "Date", cell: (e) => e.edition_date ? dayjs(e.edition_date).format("MMM D, YYYY") : "-", value: (e) => e.edition_date },
            { id: "revisions", header: "Repositories (revisions)", width: 420,
              cell: (e) => Object.entries(e.revisions || {}).map(([k, n]) => `${k} ${n}`).join(", "), value: (e) => Object.keys(e.revisions || {}).length },
            { id: "source", header: "Source", cell: (e) => e.source.split("/").pop(), value: (e) => e.source },
            { id: "imported_at", header: "Imported", cell: (e) => dateTime(e.imported_at), value: (e) => e.imported_at },
          ]} />
      </Section>
      {can() && <BuildSeed editions={q.data?.editions || []} onStarted={(id, file) => { setActiveJob({ id, what: `Building ${file}` }); setRunning(true); }} />}
      <BuiltSeeds />
    </Stack>
  );
}

function Seeds({ data, loading, error, onRetry, canChange, onStarted }: {
  data?: Packages; loading: boolean; error: unknown; onRetry: () => void; canChange: boolean; onStarted: (id: number) => void;
}) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [dvd, setDvd] = useState("latest");
  const files = data?.files || [];
  const imp = useMutation({
    mutationFn: () => api<{ job: number }>("/packages/import", { body: { files: [...sel], dvd_data: dvd } }),
    onSuccess: (x) => { notifyOk(`Import job ${x.job} started`); setSel(new Set()); onStarted(x.job); },
  });
  const toggle = (f: string, on: boolean) => setSel((s) => { const n = new Set(s); if (on) n.add(f); else n.delete(f); return n; });
  const all = files.length > 0 && sel.size === files.length;
  return (
    <Section title="Seeds in PACKAGES_DIR" p={0} actions={canChange && <>
      <Select size="xs" w={290} data={DVD} value={dvd} onChange={(v) => setDvd(v || "latest")} allowDeselect={false} aria-label="dvd-data" />
      <Button size="xs" leftSection={<IconPackageImport size={15} />} disabled={!data?.dir || !sel.size} loading={imp.isPending} onClick={() => imp.mutate()}>
        Import {sel.size ? `${sel.size} selected` : "selected"}
      </Button>
    </>}>
      {data && !data.dir && <Alert m="md" color="yellow" variant="light">No packages folder is mounted. Set PACKAGES_DIR and run rb.ps1 up.</Alert>}
      <LocalTable rows={files} loading={loading} error={error} onRetry={onRetry} rowKey={(f) => f.file} maxHeight={400}
        onRowClick={canChange ? (f) => toggle(f.file, !sel.has(f.file)) : undefined} initialSort={{ sort: "date", dir: "asc" }}
        empty={<EmptyState title="No seeds found">Put data-&lt;date&gt;.zip files in the packages folder.</EmptyState>}
        columns={[
          ...(canChange ? [{ id: "sel", width: 44,
            header: <Checkbox aria-label="Select all" checked={all} indeterminate={sel.size > 0 && !all}
              onChange={(e) => setSel(e.currentTarget.checked ? new Set(files.map((f) => f.file)) : new Set())} />,
            cell: (f: SeedFile) => <Checkbox aria-label={`Select ${f.file}`} checked={sel.has(f.file)} onChange={(e) => toggle(f.file, e.currentTarget.checked)} /> }] : []),
          { id: "file", header: "File", cell: (f) => <Text span size="sm" fw={700}>{f.file}</Text>, value: (f) => f.file },
          { id: "date", header: "Seed date", cell: (f) => f.date ? dayjs(f.date).format("MMM D, YYYY") : "-", value: (f) => f.date },
          { id: "size", header: "Size", cell: (f) => bytes(f.size), value: (f) => f.size, align: "right" },
          { id: "edition", header: "Imported as", cell: (f) => f.edition ? <Badge variant="light" color="teal">{f.edition}</Badge> : <Status kind="off">not imported</Status>, value: (f) => f.edition },
          { id: "imported_at", header: "Imported", cell: (f) => dateTime(f.imported_at), value: (f) => f.imported_at },
        ]} />
    </Section>
  );
}

function Jobs({ jobs, loading }: { jobs?: Job[]; loading: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Section title="Import and build jobs" p={0}>
      <LocalTable rows={jobs} loading={loading} rowKey={(j) => j.id} initialSort={{ sort: "id", dir: "desc" }}
        onRowClick={(j) => setOpen(open === j.id ? null : j.id)}
        empty={<EmptyState title="No jobs yet">Imports and seed builds show up here with their progress.</EmptyState>}
        columns={[
          { id: "id", header: "Job", cell: (j) => j.id, value: (j) => j.id, align: "right", width: 70 },
          { id: "source", header: "Source", cell: (j) => j.source, value: (j) => j.source, width: 300 },
          { id: "status", header: "Status", value: (j) => j.status,
            cell: (j) => j.status === "running"
              ? <Box w={130}><Progress value={100} animated striped size="sm" aria-label="Running" /><Text size="xs" fw={600} mt={2}>running</Text></Box>
              : <Status kind={j.status === "done" ? "ok" : "bad"}>{j.status}</Status> },
          { id: "last", header: "Last step", width: 380, cell: (j) => j.progress.at(-1)?.m ?? "" },
          { id: "started_at", header: "Started", cell: (j) => dateTime(j.started_at), value: (j) => j.started_at },
          { id: "finished_at", header: "Finished", cell: (j) => dateTime(j.finished_at), value: (j) => j.finished_at },
        ]} />
      {open != null && jobs?.find((j) => j.id === open) && (
        <Box p="md" style={{ borderTop: "1px solid var(--rb-line)" }}>
          <Group justify="space-between" mb="xs"><Text fw={700}>Job {open}: last steps</Text>
            <Anchor component="button" size="sm" onClick={() => setOpen(null)}>Hide</Anchor></Group>
          <div className="rb-console">
            {jobs.find((j) => j.id === open)!.progress.slice(-30).map((p, i) => <div key={i}>{dayjs(p.t).format("HH:mm:ss")}  {p.m}</div>)}
          </div>
        </Box>
      )}
    </Section>
  );
}

type BuildV = { edition: string | null; all: boolean; catalog: boolean; repos: { name: string | null; revision: string }[]; date: string | null };

function BuildSeed({ editions, onStarted }: { editions: Edition[]; onStarted: (job: number, file: string) => void }) {
  const repoNames = useQuery({ queryKey: ["updates", "repos"], queryFn: () => api<{ name: string }[]>("/updates/repos"), staleTime: 60_000 });
  const schema = z.object({
    edition: z.string().nullable(),
    all: z.boolean(),
    catalog: z.boolean(),
    repos: z.array(z.object({ name: z.string({ invalid_type_error: "Choose a repository" }).min(1, "Choose a repository"), revision: z.string() })),
    date: z.string().nullable(),
  }).superRefine((v, ctx) => {
    if (!v.all && !v.repos.length) ctx.addIssue({ code: "custom", path: ["all"], message: "Take every repository, or add the ones to include" });
  });
  const form = useForm<BuildV>({
    initialValues: { edition: null, all: true, catalog: false, repos: [], date: null },
    validate: zodResolver(schema),
  });
  const v = form.values;
  const m = useMutation({
    mutationFn: (x: BuildV) => api<{ job: number; file: string }>("/packages/build", {
      body: {
        all: x.all, edition: x.edition || null, catalog: x.catalog,
        repos: x.all ? [] : x.repos.map((r) => ({ name: r.name, revision: r.revision.trim() || null })),
        date: x.date ? dayjs(x.date).format("MMDDYYYY") : null,
      },
    }),
    onSuccess: (x) => { notifyOk(`Building ${x.file} (job ${x.job})`); onStarted(x.job, x.file); },
  });
  const names = useMemo(() => (repoNames.data || []).map((r) => r.name), [repoNames.data]);
  return (
    <Section title="Build a seed zip">
      <form onSubmit={form.onSubmit((x) => m.mutate(x))}>
        <Stack gap="md">
          <Text size="sm" c="dark.1">
            Writes <Code>data-&lt;MMDDYYYY&gt;.zip</Code> in the store format (docs/seed-zips.md) from the repositories here, at the chosen revisions.
            New package content is added with <Code>admin publish</Code> first, or with <Code>admin build-seed --add</Code>.
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Select label="History" placeholder="Served history" clearable data={editions.map((e) => e.name)} {...form.getInputProps("edition")} />
            <DateField label="Date" description="Names the zip; today when empty" value={v.date} onChange={(d) => form.setFieldValue("date", d)} />
          </SimpleGrid>
          <Switch label="Every repository at its head" {...form.getInputProps("all", { type: "checkbox" })} />
          {!v.all && (
            <Stack gap="xs">
              {v.repos.map((_, i) => (
                <Group key={i} gap="xs" align="flex-start" wrap="nowrap">
                  <Select style={{ flex: 1 }} searchable placeholder="Repository" data={names} aria-label="Repository" {...form.getInputProps(`repos.${i}.name`)} />
                  <TextInput style={{ flex: 1 }} placeholder="Revision hash or label (head when empty)" aria-label="Revision" {...form.getInputProps(`repos.${i}.revision`)} />
                  <Tooltip label="Remove"><ActionIcon variant="subtle" color="red" mt={4} aria-label="Remove repository" onClick={() => form.removeListItem("repos", i)}><IconTrash size={16} /></ActionIcon></Tooltip>
                </Group>
              ))}
              <Group>
                <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} onClick={() => form.insertListItem("repos", { name: null, revision: "" })}>Add a repository</Button>
              </Group>
            </Stack>
          )}
          {form.errors.all && <Text size="sm" c="red.4">{form.errors.all}</Text>}
          <Checkbox label="Add the catalog as it is now (a new dvd-data revision; see Catalog)" {...form.getInputProps("catalog", { type: "checkbox" })} />
          <Group justify="flex-end">
            <Button type="submit" leftSection={<IconHammer size={16} />} loading={m.isPending}>Build</Button>
          </Group>
        </Stack>
      </form>
    </Section>
  );
}

function BuiltSeeds() {
  const can = useCan();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["packages", "exports"], queryFn: () => api<Export[]>("/packages/exports") });
  const del = useMutation({
    mutationFn: (f: string) => api(`/packages/exports/${encodeURIComponent(f)}`, { method: "DELETE" }),
    onSuccess: (_x, f) => { notifyOk(`Deleted ${f}`); qc.invalidateQueries({ queryKey: ["packages", "exports"] }); },
  });
  const remove = async (f: Export) => {
    if (await confirm({ title: `Delete ${f.file}?`, body: `The built seed (${bytes(f.size)}) is deleted from the server. Build it again to get it back.`, confirmLabel: "Delete seed", danger: true })) del.mutate(f.file);
  };
  return (
    <Section title={`Built seeds${q.data ? ` (${num(q.data.length)})` : ""}`} p={0}>
      <LocalTable rows={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(f) => f.file} initialSort={{ sort: "modified", dir: "desc" }}
        empty={<EmptyState title="None yet">Build a seed zip above; it shows up here to download.</EmptyState>}
        columns={[
          { id: "file", header: "File", cell: (f) => <Anchor href={`/api/packages/exports/${encodeURIComponent(f.file)}`} fw={700}>{f.file}</Anchor>, value: (f) => f.file },
          { id: "size", header: "Size", cell: (f) => bytes(f.size), value: (f) => f.size, align: "right" },
          { id: "modified", header: "Built", cell: (f) => dateTime(f.modified), value: (f) => f.modified },
          { id: "act", header: "", cell: (f) => (
            <Group gap={6} wrap="nowrap" justify="flex-end">
              <Button size="compact-xs" variant="default" component="a" href={`/api/packages/exports/${encodeURIComponent(f.file)}`} leftSection={<IconDownload size={13} />}>Download</Button>
              {can() && <ActionIcon variant="subtle" color="red" aria-label={`Delete ${f.file}`} onClick={() => remove(f)} loading={del.isPending && del.variables === f.file}><IconTrash size={16} /></ActionIcon>}
            </Group>) },
        ]} />
    </Section>
  );
}
