import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Alert, Anchor, Badge, Button, Code, Drawer, Group, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconLock } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { dateTime, dayjs } from "@/lib/format";
import { confirm } from "@/lib/notify";
import { LocalTable } from "@/components/DataTable";
import { JobProgress, type Job } from "@/components/JobProgress";
import { PageHeader, RouteTabs, Section, Status } from "@/components/ui";
import { DataZips } from "./DataZips";
import { CatalogImport } from "./CatalogImport";

/** Admin-only operations that replace or bring in data in bulk, and the background jobs they run. */
export function AdminPage() {
  const { tab = "data-zips" } = useParams();
  const can = useCan();
  return (
    <>
      <PageHeader title="System administration" description="Bulk imports and builds: seed data zips, the catalog, the store directory. These replace data for every kiosk, so they live here rather than on the content pages." />
      {!can() && <Alert color="yellow" icon={<IconLock size={18} />} mb="md">Your account is read-only: you can watch jobs but not start them.</Alert>}
      <RouteTabs base="/admin" value={tab} tabs={[
        { value: "data-zips", label: "Data zips" }, { value: "catalog", label: "Catalog import" },
        { value: "stores", label: "Stores import" }, { value: "jobs", label: "Jobs" },
      ]} />
      {tab === "data-zips" && <DataZips />}
      {tab === "catalog" && <Section title="Import the catalog"><CatalogImport /></Section>}
      {tab === "stores" && <StoresImport />}
      {tab === "jobs" && <Jobs />}
    </>
  );
}

function StoresImport() {
  const can = useCan();
  const qc = useQueryClient();
  const [revision, setRevision] = useState("");
  const [job, setJob] = useState<number | null>(null);
  const start = useMutation({
    mutationFn: () => api<{ job: number }>("/admin/stores-import", { body: { revision } }),
    onSuccess: (r) => setJob(r.job),
  });
  const go = async () => {
    if (await confirm({ title: "Replace the store directory?", danger: true, confirmLabel: "Import stores",
      body: <Text size="sm">The Store, Banner and Market tables are replaced with the seed's. Stores, banners and markets edited here are overwritten. Titles, box art and barcodes are not touched.</Text> }))
      start.mutate();
  };
  return (
    <Section title="Import stores from seed data">
      <Stack maw={720}>
        <Text size="sm">Replaces the <b>Store</b>, <b>Banner</b> and <b>Market</b> tables with those in a seed's profile.data. The seed is a revision of the
          <Code>dvd-data</Code> repository: every data zip imported on the <Anchor component={Link} to="/admin/data-zips">Data zips</Anchor> tab adds one.
          Leave the revision empty to use the one kiosks are served now (the normal choice).</Text>
        <TextInput label="dvd-data revision" description="Optional. Empty = the served head; or a revision hash or label." value={revision}
          onChange={(e) => setRevision(e.currentTarget.value)} disabled={!can() || start.isPending || (job != null && !start.isError)} />
        {can() && <Group><Button onClick={go} loading={start.isPending} disabled={job != null}>Import stores</Button>
          {job != null && <Button variant="default" onClick={() => setJob(null)}>Start over</Button>}</Group>}
        {job != null && <JobProgress jobId={job} onFinished={() => qc.invalidateQueries({ queryKey: ["stores"] })} />}
      </Stack>
    </Section>
  );
}

function Jobs() {
  const q = useQuery({ queryKey: ["admin", "jobs"], queryFn: () => api<Job[]>("/admin/jobs?limit=100"),
    refetchInterval: (query) => (query.state.data?.some((j) => j.status === "running") ? 2000 : 15000), meta: { silent: true } });
  const [open, setOpen] = useState<Job | null>(null);
  return (
    <>
      <LocalTable<Job> rows={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => r.id} onRowClick={setOpen}
        initialSort={{ sort: "id", dir: "desc" }}
        columns={[
          { id: "id", header: "Job", value: (r) => r.id, cell: (r) => <Text fw={700} size="sm">{r.id}</Text>, width: 80 },
          { id: "source", header: "What", value: (r) => r.source, cell: (r) => r.source },
          { id: "status", header: "Status", value: (r) => r.status, cell: (r) => <Status kind={r.status === "done" ? "ok" : r.status === "failed" ? "bad" : "warn"}>{r.status}</Status> },
          { id: "last", header: "Last step", cell: (r) => r.progress.at(-1)?.m || "", width: 420 },
          { id: "started", header: "Started", value: (r) => r.started_at, cell: (r) => dateTime(r.started_at) },
          { id: "took", header: "Took", value: (r) => (r.finished_at ? dayjs(r.finished_at).diff(r.started_at, "second") : null),
            cell: (r) => r.finished_at ? `${dayjs(r.finished_at).diff(r.started_at, "second")} s` : <Badge color="yellow" variant="light">running</Badge> },
        ]} />
      <Drawer opened={!!open} onClose={() => setOpen(null)} position="right" size="lg" title={<Text fw={800}>Job {open?.id}: {open?.source}</Text>}>
        {open && <JobProgress jobId={open.id} />}
      </Drawer>
    </>
  );
}
