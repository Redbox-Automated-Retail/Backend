// Catalog import (System administration): replaces the catalog with the profile.data / inventory.data of a dvd-data
// revision (normally the served head), or of a folder inside the rb-admin container. Runs as a background job.
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Anchor, Button, Code, Group, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconDownload, IconInfoCircle } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { useUrlParam } from "@/components/ui";
import { JobProgress } from "@/components/JobProgress";

const schema = z.object({
  revision: z.string(),
  folder: z.string().refine((f) => !/\.zip\s*$/i.test(f), "A folder holding loose profile.data and inventory.data, not a .zip"),
});
type Values = z.infer<typeof schema>;

/** A panel (no page header) that imports the catalog and follows the job until it's done or failed. */
export function CatalogImport() {
  const can = useCan()("change");
  const qc = useQueryClient();
  const [job, setJob] = useUrlParam("catalogJob");
  const form = useForm<Values>({ initialValues: { revision: "", folder: "" }, validate: zodResolver(schema) });
  const run = useMutation({
    mutationFn: (v: Values) => api<{ job: number }>("/catalog/import", { body: { revision: v.revision.trim() || null, folder: v.folder.trim() || null } }),
    onSuccess: (r) => { notifyOk(`Importing the catalog (job ${r.job})`); setJob(String(r.job)); },
  });
  const submit = form.onSubmit(async (v) => {
    const from = v.folder.trim() ? `the folder ${v.folder.trim()}` : v.revision.trim() ? `dvd-data revision ${v.revision.trim()}` : "the dvd-data the kiosks are served";
    if (await confirm({
      title: "Replace the catalog?", danger: true, confirmLabel: "Import and replace",
      body: `Imports the catalog from ${from}. It replaces every title, table, box-art image and imported barcode; edits not yet published are lost. Barcodes added here or learned from kiosks are kept. About 3 minutes for a full kiosk catalog.`,
    })) run.mutate(v);
  });
  return (
    <Stack gap="md">
      <Text size="sm">
        Replaces the catalog (titles, tables, box art and imported barcodes) with the one in a dvd-data revision. Barcodes added on
        the <Anchor component={Link} to="/catalog/barcodes">Catalog</Anchor> page or learned from kiosk transactions are kept.
      </Text>
      <Alert color="blue" variant="light" icon={<IconInfoCircle size={18} />} title="Normally leave both fields empty">
        Empty imports the catalog from the dvd-data served to kiosks: the one the seed zips you imported brought in. That is the normal choice.
        Do not enter a seed zip here.
      </Alert>
      <form onSubmit={submit}>
        <Stack gap="sm">
          <TextInput label="dvd-data revision (optional)" placeholder="Empty = the served head" disabled={!can}
            description="Empty (normal): the revision kiosks are served. Or a revision hash or label." {...form.getInputProps("revision")} />
          <TextInput label="Folder (optional, advanced)" placeholder="Empty; e.g. /packages/catalog" disabled={!can}
            description={<>A folder inside the rb-admin container holding loose <Code>profile.data</Code> and <Code>inventory.data</Code>; used instead of the revision. Not a .zip.</>}
            {...form.getInputProps("folder")} />
          {can && (
            <Group justify="flex-end">
              <Button type="submit" leftSection={<IconDownload size={16} />} loading={run.isPending} color="red">Import catalog</Button>
            </Group>
          )}
        </Stack>
      </form>
      {job && (
        <Stack gap="xs">
          <Group justify="space-between"><Text fw={700}>Import job {job}</Text><Button size="xs" variant="default" onClick={() => setJob(null)}>Hide</Button></Group>
          <JobProgress jobId={Number(job)} onFinished={(j) => {
            qc.invalidateQueries({ queryKey: ["catalog"] });
            if (j.status === "done") notifyOk("Catalog imported");
          }} />
        </Stack>
      )}
    </Stack>
  );
}
