import { Alert, Button, Group, Modal, NumberInput, SegmentedControl, Select, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconAlertTriangle } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { confirm, notifyOk } from "@/lib/notify";
import { TimeField } from "@/components/fields";
import type { FileSet } from "./FileSetsPage";

type V = { revision_id: string; target_type: "kiosk" | "market" | "all"; target: string; channel: "ucs" | "legacy"; activate_start: string; activate_end: string; priority: number | string };

const hhmm = /^([01]\d|2[0-3]):[0-5]\d$/;
const schema = z.object({
  revision_id: z.string(),
  target_type: z.enum(["kiosk", "market", "all"]),
  target: z.string(),
  channel: z.enum(["ucs", "legacy"]),
  activate_start: z.string().refine((v) => !v || hhmm.test(v), "A time like 01:00"),
  activate_end: z.string().refine((v) => !v || hhmm.test(v), "A time like 05:00"),
  priority: z.number().int().min(1),
}).superRefine((v, ctx) => {
  if (v.target_type === "kiosk" && !/^\d+$/.test(v.target.trim())) ctx.addIssue({ code: "custom", path: ["target"], message: "Give a kiosk id" });
  if (v.target_type === "market" && !v.target.trim()) ctx.addIssue({ code: "custom", path: ["target"], message: "Give a market" });
  if (!!v.activate_start !== !!v.activate_end) ctx.addIssue({ code: "custom", path: [v.activate_start ? "activate_end" : "activate_start"], message: "Give both ends of the window" });
});

export function DeployDialog({ fs, onClose }: { fs: FileSet; onClose: () => void }) {
  const qc = useQueryClient();
  const form = useForm<V>({
    initialValues: { revision_id: fs.revisions[0] ? String(fs.revisions[0].id) : "", target_type: "kiosk", target: "", channel: "ucs", activate_start: "", activate_end: "", priority: 1 },
    validate: zodResolver(schema),
  });
  const m = useMutation({
    mutationFn: (v: V) => api<{ deployment: { id: number }; pushed: { delivered?: number } | null }>(`/filesets/${fs.id}/deploy`, {
      body: { revision_id: v.revision_id || null, target_type: v.target_type, target: v.target_type === "all" ? null : v.target.trim(), channel: v.channel,
        activate_start: v.activate_start || null, activate_end: v.activate_end || null, priority: Number(v.priority) || 1 },
    }),
    onSuccess: (x) => {
      notifyOk(`Deployment ${x.deployment.id}` + (x.pushed ? `, pushed to ${x.pushed.delivered ?? 0} connected kiosk(s)` : ", sent on the next server poll"));
      qc.invalidateQueries({ queryKey: ["filesets"] });
      onClose();
    },
  });
  const v = form.values;
  const submit = form.onSubmit(async (val) => {
    if (val.target_type === "all" || !val.revision_id) {
      const ok = await confirm({
        title: !val.revision_id ? "Remove the file set?" : "Deploy to every kiosk?",
        body: !val.revision_id
          ? `File set ${fs.id} (${fs.name}) is removed from ${val.target_type === "all" ? "every kiosk" : `${val.target_type} ${val.target}`}.`
          : `Revision ${val.revision_id} of ${fs.name} goes to every kiosk.`,
        confirmLabel: !val.revision_id ? "Remove it" : "Deploy to everyone", danger: true,
      });
      if (!ok) return;
    }
    m.mutate(val);
  });
  return (
    <Modal opened onClose={onClose} size="lg" title={<Text fw={800}>Deploy file set {fs.id} - {fs.name}</Text>}>
      <form onSubmit={submit}>
        <Stack gap="md">
          <Select label="Revision" allowDeselect={false}
            data={[...fs.revisions.map((r) => ({ value: String(r.id), label: `${r.id} - ${r.version}` })), { value: "", label: "(remove the file set)" }]}
            {...form.getInputProps("revision_id")} />
          {!v.revision_id && <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={18} />}>This deployment removes the file set from the target.</Alert>}
          <Stack gap={4}>
            <Text size="sm" fw={600}>Target</Text>
            <SegmentedControl fullWidth data={[{ value: "kiosk", label: "One kiosk" }, { value: "market", label: "A market" }, { value: "all", label: "All kiosks" }]}
              {...form.getInputProps("target_type")} />
          </Stack>
          {v.target_type !== "all" && (
            <TextInput label={v.target_type === "kiosk" ? "Kiosk id" : "Market"} withAsterisk {...form.getInputProps("target")} />
          )}
          <Select label="Channel" allowDeselect={false} {...form.getInputProps("channel")}
            data={[{ value: "ucs", label: "Update Client (IoT, pushed now)" }, { value: "legacy", label: "updatemgrd (u pr, next server poll)" }]} />
          <Group grow align="flex-start">
            <TimeField label="Activation window start" description="Optional; any time when empty" value={v.activate_start}
              onChange={(t) => form.setFieldValue("activate_start", t)} error={form.errors.activate_start as string | undefined} />
            <TimeField label="Activation window end" description="Kiosk local time" value={v.activate_end}
              onChange={(t) => form.setFieldValue("activate_end", t)} error={form.errors.activate_end as string | undefined} />
          </Group>
          <NumberInput label="Download priority" allowDecimal={false} min={1} w={180} {...form.getInputProps("priority")} />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button type="submit" color={v.revision_id ? undefined : "red"} loading={m.isPending}>{v.revision_id ? "Deploy" : "Deploy removal"}</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
