import { Badge, Code, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { dateTime, num, short } from "@/lib/format";
import { EmptyState, LocalTable } from "@/components/DataTable";
import { PageHeader, Section, Status, useUrlParam } from "@/components/ui";
import { RepoDrawer } from "./RepoDrawer";
import { StoreMatrix } from "./StoreMatrix";

export type UpdRepo = {
  name: string; auto_subscribe: boolean; serve_head: string | null; canonical_edition: string | null; revisions: number;
  head: string | null; label: string | null; head_created: string | null; snapshot_files: number; stores_current: number;
  stores_reporting: number; released: boolean | null; rollouts: number; served: string | null;
};

export function UpdateServicePage() {
  const [open, setOpen] = useUrlParam("repo");
  const q = useQuery({ queryKey: ["updates", "repos"], queryFn: () => api<UpdRepo[]>("/updates/repos") });
  const repo = q.data?.find((r) => r.name === open) || null;
  return (
    <>
      <PageHeader stats="updates" title="Update service" description="updatemgrd: kiosks poll every 15 minutes (u p). A kiosk without a repository gets a full snapshot; one at an older revision gets the later revisions." />
      <Section title="Repositories" p={0}>
        <LocalTable rows={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => r.name}
          onRowClick={(r) => setOpen(r.name)} maxHeight={520} initialSort={{ sort: "name", dir: "asc" }}
          empty={<EmptyState title="No repositories yet">Import a seed zip on the System administration page, or publish a release.</EmptyState>}
          columns={[
            { id: "name", header: "Repository", cell: (r) => <Text span size="sm" fw={700}>{r.name}</Text>, value: (r) => r.name },
            { id: "revisions", header: "Revisions", cell: (r) => num(r.revisions), value: (r) => r.revisions, align: "right" },
            { id: "head", header: "Head", cell: (r) => r.head ? <Code title={r.head}>{short(r.head)}</Code> : "-", value: (r) => r.head },
            { id: "label", header: "Label", cell: (r) => r.label, value: (r) => r.label, width: 220 },
            { id: "served", header: "Served", value: (r) => r.serve_head || "",
              cell: (r) => r.serve_head ? <Badge color="yellow" variant="light">pinned {short(r.serve_head)}</Badge>
                : r.served && r.served !== r.head ? <Badge color="grape" variant="light">release {short(r.served)}</Badge> : "newest" },
            { id: "rollouts", header: "Rollouts", cell: (r) => num(r.rollouts), value: (r) => r.rollouts, align: "right" },
            { id: "canonical_edition", header: "From edition", cell: (r) => r.canonical_edition, value: (r) => r.canonical_edition },
            { id: "head_created", header: "Head added", cell: (r) => dateTime(r.head_created), value: (r) => r.head_created },
            { id: "snapshot_files", header: "Snapshot files", cell: (r) => num(r.snapshot_files), value: (r) => r.snapshot_files, align: "right" },
            { id: "stores_current", header: "Stores current", cell: (r) => `${num(r.stores_current)} / ${num(r.stores_reporting)}`, value: (r) => r.stores_current, align: "right" },
            { id: "auto_subscribe", header: "Auto-subscribe", cell: (r) => r.auto_subscribe ? <Status kind="ok">yes</Status> : <Status kind="off">no</Status>, value: (r) => (r.auto_subscribe ? 1 : 0) },
          ]} />
      </Section>
      <div style={{ height: 16 }} />
      <StoreMatrix />
      <RepoDrawer repo={repo} name={open} onClose={() => setOpen(null)} />
    </>
  );
}
