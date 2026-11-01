import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Button, Code, Drawer, Group, Select, Skeleton, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconRefresh, IconSearch, IconUpload } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { dateTime, num, short } from "@/lib/format";
import { notifyOk } from "@/lib/notify";
import { EmptyState, ErrorState, LocalTable } from "@/components/DataTable";
import { Section } from "@/components/ui";
import { appendFiles, FilesPicker, UploadProgress, uploadForm } from "../releases/upload";
import type { UpdRepo } from "./UpdateServicePage";

type Detail = {
  revisions: { seq: number; hash: string; label: string | null; created_at: string; changes: number; seeds: number; deltas: number }[];
  files: { target_path: string; target_name: string; version_hash: string }[];
};
type SnapResult = { files: number; source?: string; problems: unknown[] };

const DEFAULT_TARGET = "C:\\Program Files\\Redbox\\REDS\\";

export function RepoDrawer({ repo, name, onClose }: { repo: UpdRepo | null; name: string | null; onClose: () => void }) {
  return (
    <Drawer opened={!!name} onClose={onClose} position="right" size="xl" title={<Text fw={800} fz="lg">{name}</Text>}>
      {name && <Body key={name} name={name} repo={repo} />}
    </Drawer>
  );
}

function Body({ name, repo }: { name: string; repo: UpdRepo | null }) {
  const can = useCan();
  const qc = useQueryClient();
  const enc = encodeURIComponent(name);
  const q = useQuery({ queryKey: ["updates", "repo", name], queryFn: () => api<Detail>(`/updates/repos/${enc}`) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["updates"] });
  const [fileFilter, setFileFilter] = useState("");

  // publish a new revision
  const [files, setFiles] = useState<File[]>([]);
  const [folder, setFolder] = useState(false);
  const [pct, setPct] = useState<number | null>(null);
  const form = useForm({
    initialValues: { target_path: DEFAULT_TARGET, label: "" },
    validate: zodResolver(z.object({ target_path: z.string().regex(/^[A-Za-z]:\\/, "An absolute Windows folder, like C:\\Program Files\\Redbox\\REDS\\"), label: z.string() })),
  });
  useEffect(() => {
    const tp = q.data?.files[0]?.target_path;
    if (tp && !form.isDirty("target_path")) {
      form.setInitialValues({ ...form.values, target_path: tp });
      form.setFieldValue("target_path", tp);
    }
  }, [q.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const publish = useMutation({
    mutationFn: (v: { target_path: string; label: string }) => {
      const fd = new FormData();
      fd.append("target_path", v.target_path);
      fd.append("label", v.label);
      appendFiles(fd, "files", files);
      setPct(0);
      return uploadForm<{ revision: string | null; files: number }>(`/updates/repos/${enc}/publish`, fd, setPct);
    },
    onSuccess: (x) => {
      notifyOk(x.revision ? `Published revision ${x.revision.slice(0, 10)} (${x.files} files)` : "No file changed; nothing published");
      setFiles([]);
      form.setFieldValue("label", "");
      refresh();
    },
    onSettled: () => setPct(null),
  });

  // serve pin
  const [pin, setPin] = useState<string | null>(repo?.serve_head || "");
  useEffect(() => setPin(repo?.serve_head || ""), [repo?.serve_head]);
  const pinM = useMutation({
    mutationFn: (rev: string | null) => api<SnapResult>(`/updates/repos/${enc}/pin`, { body: { revision: rev || null } }),
    onSuccess: (x, rev) => { notifyOk(`Serving ${rev ? rev.slice(0, 10) : "the newest revision"}; snapshot ${x.files} files from ${x.source}, ${x.problems.length} problems`); refresh(); },
  });
  const snap = useMutation({
    mutationFn: () => api<SnapResult>(`/updates/repos/${enc}/snapshot`, { body: {} }),
    onSuccess: (x) => { notifyOk(`Snapshot: ${x.files} files, ${x.problems.length} problems`); refresh(); },
  });
  const auto = useMutation({
    mutationFn: (v: boolean) => api(`/updates/repos/${enc}`, { method: "PUT", body: { auto_subscribe: v } }),
    onSuccess: (_x, v) => { notifyOk(v ? "Offered to every store automatically" : "No longer offered automatically"); refresh(); },
  });

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  const shownFiles = (d?.files || []).filter((f) => !fileFilter || `${f.target_path}\\${f.target_name}`.toLowerCase().includes(fileFilter.toLowerCase()));
  return (
    <Stack gap="md">
      {can() && (
        <Section title="Publish a new revision">
          <form onSubmit={form.onSubmit((v) => publish.mutate(v))}>
            <Stack gap="sm">
              <Text size="sm" c="dark.1">
                Unchanged files are skipped; changed ones go out as VCDIFF deltas when smaller. Sub-folders are kept when you pick a folder.
                A revision published here is served to every kiosk at once; to publish a build without serving it and roll it out in stages,
                use <Anchor component={Link} to={`/releases/${enc}`}>Releases</Anchor>.
              </Text>
              {d ? <TextInput label="Target folder on the kiosk" withAsterisk {...form.getInputProps("target_path")} /> : <Skeleton h={58} />}
              <TextInput label="Label" placeholder={`${name}-release`} {...form.getInputProps("label")} />
              <FilesPicker label="Files" files={files} onChange={setFiles} folder={folder} onFolderChange={setFolder} disabled={publish.isPending} />
              <UploadProgress pct={pct} />
              <Group justify="flex-end">
                <Button type="submit" leftSection={<IconUpload size={16} />} disabled={!files.length} loading={publish.isPending}>Publish</Button>
              </Group>
            </Stack>
          </form>
        </Section>
      )}

      <Section title="Serving">
        <Stack gap="sm">
          <Group align="flex-end" gap="sm">
            <Select label="Serve to kiosks" style={{ flex: 1 }} allowDeselect={false} disabled={!can() || !d} value={pin} onChange={(v) => setPin(v ?? "")}
              data={[{ value: "", label: "Newest revision" }, ...(d?.revisions || []).map((v) => ({ value: v.hash, label: `${v.seq}  ${short(v.hash)}  ${v.label || ""}` }))]} />
            {can() && <Button onClick={() => pinM.mutate(pin || null)} loading={pinM.isPending} disabled={(pin || "") === (repo?.serve_head || "")}>Apply</Button>}
          </Group>
          <Text size="sm" c="dark.1">Kiosks below it are updated up to it, kiosks without the repository get its snapshot, kiosks past it are left alone.</Text>
          <Group justify="space-between">
            <Switch label="Offer to every store automatically (auto-subscribe)" checked={!!repo?.auto_subscribe} disabled={!can() || auto.isPending}
              onChange={(e) => auto.mutate(e.currentTarget.checked)} />
            {can() && <Button variant="default" leftSection={<IconRefresh size={16} />} onClick={() => snap.mutate()} loading={snap.isPending}>Rebuild snapshot</Button>}
          </Group>
        </Stack>
      </Section>

      <Section title="Latest revisions" p={0}>
        <LocalTable rows={d?.revisions} loading={!d} rowKey={(r) => r.hash} maxHeight={320} initialSort={{ sort: "seq", dir: "desc" }}
          empty={<EmptyState title="No revisions">Publish one above.</EmptyState>}
          columns={[
            { id: "seq", header: "Seq", cell: (r) => r.seq, value: (r) => r.seq, align: "right" },
            { id: "hash", header: "Hash", cell: (r) => <Code title={r.hash}>{short(r.hash)}</Code>, value: (r) => r.hash },
            { id: "label", header: "Label", cell: (r) => r.label, value: (r) => r.label, width: 200 },
            { id: "changes", header: "Changes", cell: (r) => num(r.changes), value: (r) => r.changes, align: "right" },
            { id: "seeds", header: "Seeds", cell: (r) => num(r.seeds), value: (r) => r.seeds, align: "right" },
            { id: "deltas", header: "Deltas", cell: (r) => num(r.deltas), value: (r) => r.deltas, align: "right" },
            { id: "created_at", header: "Added", cell: (r) => dateTime(r.created_at), value: (r) => r.created_at },
          ]} />
      </Section>

      <Section title={`Files at head${d ? ` (${num(d.files.length)})` : ""}`} p={0}
        actions={<TextInput size="xs" placeholder="Filter files" value={fileFilter} onChange={(e) => setFileFilter(e.currentTarget.value)} leftSection={<IconSearch size={14} />} aria-label="Filter files" />}>
        <LocalTable rows={shownFiles} loading={!d} rowKey={(r) => `${r.target_path}\\${r.target_name}`} maxHeight={400}
          empty={<EmptyState title="No files">The head revision has no snapshot yet; rebuild it.</EmptyState>}
          columns={[
            { id: "target_path", header: "Folder", cell: (r) => r.target_path, value: (r) => r.target_path, width: 280 },
            { id: "target_name", header: "File", cell: (r) => r.target_name, value: (r) => r.target_name },
            { id: "version_hash", header: "SHA-1", cell: (r) => <Code title={r.version_hash}>{short(r.version_hash)}</Code>, value: (r) => r.version_hash },
          ]} />
      </Section>
    </Stack>
  );
}
