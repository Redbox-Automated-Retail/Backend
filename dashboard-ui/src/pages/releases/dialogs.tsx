import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Anchor, Button, Checkbox, Code, FileInput, Group, Modal, ScrollArea, SegmentedControl, Select, Stack, Text } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconAlertTriangle, IconFileZip } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { confirm, notifyOk } from "@/lib/notify";
import { short } from "@/lib/format";
import { Facts } from "@/components/ui";
import { UploadProgress, uploadForm } from "./upload";
import { targetText, type RepoStatus, type TargetKind } from "./types";

export type TargetPreset = { kind?: TargetKind; store?: string | null; market?: string | null };

const targetSchema = {
  kind: z.enum(["store", "market", "all"]),
  store: z.string().nullable(),
  market: z.string().nullable(),
};
type TargetValues = { kind: TargetKind; store: string | null; market: string | null };
const requireTarget = (v: TargetValues, ctx: z.RefinementCtx) => {
  if (v.kind === "store" && !v.store) ctx.addIssue({ code: "custom", path: ["store"], message: "Choose a store" });
  if (v.kind === "market" && !v.market) ctx.addIssue({ code: "custom", path: ["market"], message: "Choose a market" });
};
const targetBody = (v: TargetValues) => (v.kind === "all" ? { all: true } : v.kind === "market" ? { market: v.market } : { store: v.store });

const revLabel = (r: RepoStatus["revisions"][number], withState = true) =>
  `${r.seq}  ${short(r.hash)}  ${r.version || r.label || ""}${withState && !r.released ? "  (unreleased)" : ""}`;

function TargetFields({ form, st }: { form: any; st: RepoStatus }) {
  const kind = form.values.kind as TargetKind;
  return (
    <>
      <Stack gap={4}>
        <Text size="sm" fw={600}>Target</Text>
        <SegmentedControl fullWidth {...form.getInputProps("kind")}
          data={[{ value: "store", label: "One store" }, { value: "market", label: "A market" }, { value: "all", label: "Every store" }]} />
      </Stack>
      {kind === "store" && (
        <Select label="Store" searchable placeholder="Pick a store" nothingFoundMessage="No store has polled for this repository"
          data={st.stores.map((s) => ({ value: s.store, label: s.market ? `${s.store} (${s.market})` : s.store }))} {...form.getInputProps("store")} />
      )}
      {kind === "market" && (
        <Select label="Market" searchable placeholder="Pick a market" nothingFoundMessage="No kiosk has a market"
          data={st.markets} {...form.getInputProps("market")} />
      )}
      {kind === "all" && (
        <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={18} />}>
          This changes what every store is served. You will be asked to confirm.
        </Alert>
      )}
    </>
  );
}

const confirmAll = (name: string, v: TargetValues, verb: string) =>
  v.kind !== "all" ? Promise.resolve(true) : confirm({
    title: `${verb} for every store?`,
    body: `This changes what EVERY store is served for ${name}. Store and market rollouts at or below it are removed.`,
    confirmLabel: `${verb} for everyone`, danger: true,
  });

export function RolloutDialog({ name, st, hash, preset, opened, onClose }: {
  name: string; st: RepoStatus; hash?: string | null; preset?: TargetPreset; opened: boolean; onClose: () => void;
}) {
  const qc = useQueryClient();
  const schema = z.object({ revision: z.string({ invalid_type_error: "Choose a revision" }).min(1, "Choose a revision"), ...targetSchema }).superRefine(requireTarget);
  const form = useForm({
    initialValues: { revision: hash || st.revisions[0]?.hash || "", kind: preset?.kind || "store", store: preset?.store ?? null, market: preset?.market ?? null } as TargetValues & { revision: string },
    validate: zodResolver(schema),
  });
  useEffect(() => {
    if (opened) form.setValues({ revision: hash || st.revisions[0]?.hash || "", kind: preset?.kind || "store", store: preset?.store ?? null, market: preset?.market ?? null });
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps
  const m = useMutation({
    mutationFn: (v: TargetValues & { revision: string }) =>
      api<{ target: string; name: string | null; head: string; label: string | null }>(`/releases/repo/${encodeURIComponent(name)}/rollout`, { body: { revision: v.revision, ...targetBody(v) } }),
    onSuccess: (x) => {
      notifyOk(`${targetText(x.target, x.name)} now gets ${x.label || x.head.slice(0, 10)}`, "Rolled out");
      qc.invalidateQueries({ queryKey: ["releases"] });
      qc.invalidateQueries({ queryKey: ["updates"] });
      onClose();
    },
  });
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={800}>Roll out {name}</Text>} size="lg">
      <form onSubmit={form.onSubmit(async (v) => { if (await confirmAll(name, v, "Roll out")) m.mutate(v); })}>
        <Stack gap="md">
          <Select label="Revision" searchable allowDeselect={false} data={st.revisions.map((r) => ({ value: r.hash, label: revLabel(r) }))} {...form.getInputProps("revision")} />
          <TargetFields form={form} st={st} />
          <Text size="sm" c="dark.1">
            Kiosks below it get every revision up to it; kiosks without the repository get its snapshot. Every store: it is released and
            pinned, and store and market rollouts at or below it are removed.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button type="submit" loading={m.isPending}>Roll out</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

type RollbackResult = { target: string; name: string | null; from: string; to: string; head: string; revert: string | null; stores_ahead: string[]; not_removed?: string[] };

export function RollbackDialog({ name, st, preset, opened, onClose }: { name: string; st: RepoStatus; preset?: TargetPreset; opened: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [result, setResult] = useState<RollbackResult | null>(null);
  const schema = z.object({ to: z.string().nullable(), ...targetSchema }).superRefine(requireTarget);
  const init = () => ({ to: null as string | null, kind: preset?.kind || "store", store: preset?.store ?? null, market: preset?.market ?? null });
  const form = useForm({ initialValues: init() as TargetValues & { to: string | null }, validate: zodResolver(schema) });
  useEffect(() => { if (opened) { form.setValues(init()); setResult(null); } }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps
  const m = useMutation({
    mutationFn: (v: TargetValues & { to: string | null }) =>
      api<RollbackResult>(`/releases/repo/${encodeURIComponent(name)}/rollback`, { body: { to: v.to || null, ...targetBody(v) } }),
    onSuccess: (x) => {
      notifyOk(x.revert ? `Published revert ${x.revert.slice(0, 10)} for ${x.stores_ahead.length} store(s) that had moved on` : "Pointed back; no store had taken it yet", "Rolled back");
      qc.invalidateQueries({ queryKey: ["releases"] });
      qc.invalidateQueries({ queryKey: ["updates"] });
      if (x.not_removed && x.not_removed.length) setResult(x); else onClose();
    },
  });
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={800}>Roll back {name}</Text>} size="lg">
      {result ? (
        <Stack gap="md">
          <Alert color="yellow" icon={<IconAlertTriangle size={18} />} title="Some files stay on the kiosks">
            These files were added after {short(result.to)} and are not removed (updatemgrd cannot delete files):
          </Alert>
          <ScrollArea.Autosize mah={260}>
            <Code block>{result.not_removed!.join("\n")}</Code>
          </ScrollArea.Autosize>
          <Group justify="flex-end"><Button onClick={onClose}>OK</Button></Group>
        </Stack>
      ) : (
        <form onSubmit={form.onSubmit(async (v) => { if (await confirmAll(name, v, "Roll back")) m.mutate(v); })}>
          <Stack gap="md">
            <TargetFields form={form} st={st} />
            <Select label="Back to" clearable placeholder="What it had before its last rollout"
              data={st.revisions.map((r) => ({ value: r.hash, label: revLabel(r, false) }))} {...form.getInputProps("to")} />
            <Text size="sm" c="dark.1">
              If no store of the target has taken the newer revision yet, the target is pointed back. If some have, a revert revision is
              published with the older files and served to the target, because updatemgrd only moves forward. Files a release added are not removed.
            </Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={onClose}>Cancel</Button>
              <Button type="submit" color="red" loading={m.isPending}>Roll back</Button>
            </Group>
          </Stack>
        </form>
      )}
    </Modal>
  );
}

type PublishResult = Record<string, unknown> & { repository?: string; revision?: string | number | null; label?: string; fileset?: number; note?: string };

export function PublishDialog({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [allowNew, setAllowNew] = useState(false);
  const [pct, setPct] = useState<number | null>(null);
  const [result, setResult] = useState<PublishResult | null>(null);
  useEffect(() => { if (opened) { setFile(null); setAllowNew(false); setPct(null); setResult(null); } }, [opened]);
  const m = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      fd.append("allow_new", allowNew ? "true" : "false");
      setPct(0);
      return uploadForm<PublishResult>("/releases/upload", fd, setPct);
    },
    onSuccess: (x) => {
      notifyOk(x.fileset ? `File set ${x.fileset} revision ${x.revision} created` : x.revision ? `Published ${x.label} (unreleased)` : "Nothing changed");
      setResult(x);
      qc.invalidateQueries({ queryKey: ["releases"] });
      qc.invalidateQueries({ queryKey: ["updates"] });
      qc.invalidateQueries({ queryKey: ["filesets"] });
    },
    onSettled: () => setPct(null),
  });
  const show = (v: unknown) => (Array.isArray(v) ? v.join(", ") || "-" : v == null || v === "" ? "-" : String(v));
  return (
    <Modal opened={opened} onClose={() => !m.isPending && onClose()} title={<Text fw={800}>Publish a release</Text>} size="lg">
      <Stack gap="md">
        <Text size="sm">
          A zip from the Software repository's <Code>build/release.ps1</Code>. It is published <b>unreleased</b>: nothing reaches a kiosk until
          you roll it out. File-set releases become file-set revisions (deploy those on <Anchor component={Link} to="/filesets" onClick={onClose}>File sets</Anchor>).
        </Text>
        <FileInput label="Release zip" withAsterisk accept=".zip,application/zip" placeholder="Choose a .zip" leftSection={<IconFileZip size={16} />}
          value={file} onChange={setFile} clearable disabled={m.isPending} />
        <Checkbox label="Also add files the repository does not have yet (allow_new)" checked={allowNew} disabled={m.isPending}
          onChange={(e) => setAllowNew(e.currentTarget.checked)} />
        <UploadProgress pct={pct} />
        {result && (
          <Stack gap="sm" p="md" style={{ border: "1px solid var(--rb-line)", borderRadius: 8 }}>
            {result.note && <Alert color="yellow" variant="light">{result.note}</Alert>}
            <Facts cols={3} items={Object.entries(result).filter(([k]) => k !== "note").map(([k, v]) => [k.replace(/_/g, " "), show(v)])} />
            <Group gap="sm">
              {result.repository && result.revision && (
                <Button component={Link} to={`/releases/${encodeURIComponent(result.repository)}`} onClick={onClose}>Roll it out</Button>
              )}
              {result.fileset && <Button component={Link} to="/filesets" onClick={onClose}>Deploy it</Button>}
            </Group>
          </Stack>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={m.isPending}>{result ? "Close" : "Cancel"}</Button>
          <Button onClick={() => m.mutate()} disabled={!file} loading={m.isPending}>Publish</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
