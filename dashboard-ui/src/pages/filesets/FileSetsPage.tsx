import { useState } from "react";
import { Badge, Button, Group, Modal, NumberInput, Paper, Skeleton, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconPlus, IconRocket, IconUpload } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { dateTime, num } from "@/lib/format";
import { notifyOk } from "@/lib/notify";
import { EmptyState, ErrorState, LocalTable } from "@/components/DataTable";
import { PageHeader, Section, Status } from "@/components/ui";
import { RevisionDrawer } from "./RevisionDrawer";
import { DeployDialog } from "./DeployDialog";

export type FsRevision = { id: number; name: string; version: string; created_at: string; files: number };
export type FsDeployment = {
  id: number; revision_id: number | null; target_type: "all" | "market" | "kiosk"; target: string | null; channel: "ucs" | "legacy";
  activate_start: string | null; activate_end: string | null; download_priority: number; created_at: string;
};
export type FsState = { kiosk_id: number; channel: string; revision_id: number | null; state: number; updated_at: string };
export type FileSet = { id: number; name: string; revisions: FsRevision[]; deployments: FsDeployment[]; states: FsState[] };

const STATES: [string, "ok" | "warn" | "bad"][] = [["Active", "ok"], ["InProgress", "warn"], ["NeedsDependency", "warn"], ["Error", "bad"]];

export function FileSetsPage() {
  const can = useCan();
  const q = useQuery({ queryKey: ["filesets"], queryFn: () => api<FileSet[]>("/filesets") });
  const [create, setCreate] = useState(false);
  const [rev, setRev] = useState<FileSet | null>(null);
  const [dep, setDep] = useState<FileSet | null>(null);
  return (
    <>
      <PageHeader stats="filesets" title="File sets"
        description={`File sets go to the Update Client over IoT ("ucs" channel, pushed immediately) or to updatemgrd's server poll ("legacy").`}
        actions={can() && <Button leftSection={<IconPlus size={16} />} onClick={() => setCreate(true)}>Create file set</Button>} />
      {q.isError ? <Paper bg="dark.7"><ErrorState error={q.error} onRetry={() => q.refetch()} /></Paper>
        : q.isPending ? <Stack gap="md"><Skeleton h={300} /><Skeleton h={300} /></Stack>
        : !q.data.length ? <Paper bg="dark.7"><EmptyState title="No file sets yet">Create one, or publish a file-set release on the Releases page.</EmptyState></Paper>
        : (
          <Stack gap="lg">
            {q.data.map((s) => <FileSetCard key={s.id} s={s} onRevision={() => setRev(s)} onDeploy={() => setDep(s)} />)}
          </Stack>
        )}
      <CreateDialog opened={create} onClose={() => setCreate(false)} existing={(q.data || []).map((s) => s.id)} />
      <RevisionDrawer fs={rev} onClose={() => setRev(null)} />
      {dep && <DeployDialog fs={dep} onClose={() => setDep(null)} />}
    </>
  );
}

function FileSetCard({ s, onRevision, onDeploy }: { s: FileSet; onRevision: () => void; onDeploy: () => void }) {
  const can = useCan();
  const revName = (id: number | null) => {
    if (id == null) return "-";
    const r = s.revisions.find((x) => x.id === id);
    return r ? `${id} (${r.version})` : String(id);
  };
  return (
    <Section title={<Group gap="sm"><Text fw={800} fz="lg">{s.id} - {s.name}</Text><Badge variant="light" color="gray">{num(s.revisions.length)} revisions</Badge></Group>}
      actions={can() && <>
        <Button size="xs" variant="default" leftSection={<IconUpload size={15} />} onClick={onRevision}>New revision</Button>
        <Button size="xs" leftSection={<IconRocket size={15} />} onClick={onDeploy}>Deploy</Button>
      </>}>
      <Stack gap="md">
        <div>
          <Text fw={700} mb={6}>Revisions</Text>
          <LocalTable rows={s.revisions} rowKey={(r) => r.id} maxHeight={260} initialSort={{ sort: "id", dir: "desc" }}
            empty={<EmptyState title="No revisions">Upload one with New revision.</EmptyState>}
            columns={[
              { id: "id", header: "Id", cell: (r) => r.id, value: (r) => r.id, align: "right", width: 70 },
              { id: "version", header: "Version", cell: (r) => <Text span size="sm" fw={700}>{r.version}</Text>, value: (r) => r.version },
              { id: "name", header: "Name", cell: (r) => r.name, value: (r) => r.name },
              { id: "files", header: "Files", cell: (r) => num(r.files), value: (r) => r.files, align: "right" },
              { id: "created_at", header: "Created", cell: (r) => dateTime(r.created_at), value: (r) => r.created_at },
            ]} />
        </div>
        <div>
          <Text fw={700} mb={6}>Deployments</Text>
          <LocalTable rows={s.deployments} rowKey={(r) => r.id} maxHeight={260} initialSort={{ sort: "id", dir: "desc" }}
            empty={<EmptyState title="Not deployed">Deploy a revision to a kiosk, a market or every kiosk.</EmptyState>}
            columns={[
              { id: "id", header: "Id", cell: (r) => r.id, value: (r) => r.id, align: "right", width: 70 },
              { id: "revision_id", header: "Revision", cell: (r) => r.revision_id ? revName(r.revision_id) : <Status kind="warn">remove</Status>, value: (r) => r.revision_id ?? -1 },
              { id: "channel", header: "Channel", cell: (r) => r.channel === "ucs" ? "Update Client (IoT)" : "updatemgrd", value: (r) => r.channel },
              { id: "target", header: "Target", cell: (r) => r.target_type === "all" ? "all kiosks" : `${r.target_type} ${r.target ?? ""}`, value: (r) => `${r.target_type} ${r.target}` },
              { id: "window", header: "Window", cell: (r) => r.activate_start ? `${r.activate_start} - ${r.activate_end ?? ""}` : "any time", value: (r) => r.activate_start },
              { id: "created_at", header: "Created", cell: (r) => dateTime(r.created_at), value: (r) => r.created_at },
            ]} />
        </div>
        <div>
          <Text fw={700} mb={6}>Kiosk states</Text>
          <LocalTable rows={s.states} rowKey={(r) => `${r.kiosk_id}:${r.channel}`} maxHeight={320}
            empty={<EmptyState title="No kiosk has reported">Kiosks report their file-set state after a deployment reaches them.</EmptyState>}
            columns={[
              { id: "kiosk_id", header: "Kiosk", cell: (r) => r.kiosk_id, value: (r) => r.kiosk_id },
              { id: "channel", header: "Channel", cell: (r) => r.channel, value: (r) => r.channel },
              { id: "revision_id", header: "Revision", cell: (r) => revName(r.revision_id), value: (r) => r.revision_id },
              { id: "state", header: "State", cell: (r) => STATES[r.state] ? <Status kind={STATES[r.state][1]}>{STATES[r.state][0]}</Status> : String(r.state), value: (r) => r.state },
              { id: "updated_at", header: "Reported", cell: (r) => dateTime(r.updated_at), value: (r) => r.updated_at },
            ]} />
        </div>
      </Stack>
    </Section>
  );
}

function CreateDialog({ opened, onClose, existing }: { opened: boolean; onClose: () => void; existing: number[] }) {
  const qc = useQueryClient();
  const schema = z.object({
    id: z.number({ invalid_type_error: "Give the file set a number" }).int().positive("A positive number").refine((v) => !existing.includes(v), "That id is taken"),
    name: z.string().trim().min(1, "Give it a name"),
    retention_days: z.number().int().min(1),
    retention_revisions: z.number().int().min(1),
  });
  const form = useForm<{ id: number | string; name: string; retention_days: number | string; retention_revisions: number | string }>({
    initialValues: { id: "", name: "", retention_days: 30, retention_revisions: 3 },
    validate: zodResolver(schema),
  });
  const m = useMutation({
    mutationFn: (v: typeof form.values) => api("/filesets", { body: v }),
    onSuccess: () => { notifyOk("File set created"); qc.invalidateQueries({ queryKey: ["filesets"] }); form.reset(); onClose(); },
  });
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={800}>Create a file set</Text>}>
      <form onSubmit={form.onSubmit((v) => m.mutate(v))}>
        <Stack gap="md">
          <NumberInput label="Id" withAsterisk allowDecimal={false} min={1} description="The FileSetId the kiosks know it by" {...form.getInputProps("id")} />
          <TextInput label="Name" withAsterisk {...form.getInputProps("name")} />
          <Group grow>
            <NumberInput label="Keep revisions for (days)" allowDecimal={false} min={1} {...form.getInputProps("retention_days")} />
            <NumberInput label="Keep at least (revisions)" allowDecimal={false} min={1} {...form.getInputProps("retention_revisions")} />
          </Group>
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!form.isDirty()} loading={m.isPending}>Create</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
