import { useEffect, useState } from "react";
import { ActionIcon, Button, Code, Drawer, Group, NumberInput, Select, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconPlus, IconTrash, IconUpload } from "@tabler/icons-react";
import { notifyOk } from "@/lib/notify";
import { Section } from "@/components/ui";
import { appendFiles, FilesPicker, UploadProgress, uploadForm } from "../releases/upload";
import type { FileSet } from "./FileSetsPage";

type Dep = { fileset: number | string; type: string; min: string; max: string };
const DEP_TYPES = [{ value: "0", label: "Version" }, { value: "1", label: "String" }, { value: "2", label: "Long" }];

const schema = z.object({
  version: z.string().trim().min(1, "Give a version, like 1.0.0.0"),
  name: z.string(),
  destination: z.string().regex(/^[A-Za-z]:\\/, "An absolute Windows folder, like C:\\Program Files\\Redbox\\REDS\\"),
  deps: z.array(z.object({
    fileset: z.number({ invalid_type_error: "File set id" }).int().positive("File set id"),
    type: z.string(),
    min: z.string(),
    max: z.string(),
  })),
});

export function RevisionDrawer({ fs, onClose }: { fs: FileSet | null; onClose: () => void }) {
  return (
    <Drawer opened={!!fs} onClose={onClose} position="right" size="lg" title={<Text fw={800} fz="lg">New revision of file set {fs?.id} - {fs?.name}</Text>}>
      {fs && <Body key={fs.id} fs={fs} onClose={onClose} />}
    </Drawer>
  );
}

function Body({ fs, onClose }: { fs: FileSet; onClose: () => void }) {
  const qc = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [folder, setFolder] = useState(false);
  const [pct, setPct] = useState<number | null>(null);
  const [filesError, setFilesError] = useState<string | undefined>();
  useEffect(() => { if (files.length) setFilesError(undefined); }, [files]);
  const form = useForm<{ version: string; name: string; destination: string; deps: Dep[] }>({
    initialValues: { version: "", name: "", destination: "C:\\Program Files\\Redbox\\REDS\\", deps: [] },
    validate: zodResolver(schema),
  });
  const m = useMutation({
    mutationFn: (v: typeof form.values) => {
      const fd = new FormData();
      fd.append("version", v.version.trim());
      fd.append("name", v.name);
      fd.append("destination", v.destination);
      const deps = v.deps.map((d) => ({
        DependsOnFileSetId: Number(d.fileset), DependencyType: Number(d.type),
        ...(d.min ? { MinimumVersion: d.min } : {}), ...(d.max ? { MaximumVersion: d.max } : {}),
      }));
      fd.append("dependencies", deps.length ? JSON.stringify(deps) : "");
      appendFiles(fd, "files", files);
      setPct(0);
      return uploadForm<{ revision: number }>(`/filesets/${fs.id}/revisions`, fd, setPct);
    },
    onSuccess: (x) => { notifyOk(`Revision ${x.revision} created`); qc.invalidateQueries({ queryKey: ["filesets"] }); onClose(); },
    onSettled: () => setPct(null),
  });
  const submit = form.onSubmit((v) => {
    if (!files.length) return setFilesError("Choose the files of this revision");
    m.mutate(v);
  });
  return (
    <form onSubmit={submit}>
      <Stack gap="md">
        <Group grow align="flex-start">
          <TextInput label="Version" withAsterisk placeholder="1.0.0.0" {...form.getInputProps("version")} />
          <TextInput label="Name" placeholder="defaults to the version" {...form.getInputProps("name")} />
        </Group>
        <TextInput label="Destination folder on the kiosk" withAsterisk {...form.getInputProps("destination")} />
        <FilesPicker label="Files" files={files} onChange={setFiles} folder={folder} onFolderChange={setFolder} error={filesError} disabled={m.isPending}
          description="Include a fileset.json to name a Windows service to stop and start, and beforeactivate.ps1 / afteractivate.ps1 scripts if needed." />
        <Section title="Dependencies" p="sm" actions={
          <Button size="xs" variant="default" leftSection={<IconPlus size={14} />}
            onClick={() => form.insertListItem("deps", { fileset: "", type: "0", min: "", max: "" })}>Add</Button>}>
          {form.values.deps.length ? (
            <Stack gap="xs">
              {form.values.deps.map((_, i) => (
                <Group key={i} gap="xs" align="flex-start" wrap="nowrap">
                  <NumberInput w={110} label={i === 0 ? "File set" : undefined} aria-label="Depends on file set" allowDecimal={false} min={1} {...form.getInputProps(`deps.${i}.fileset`)} />
                  <Select w={110} label={i === 0 ? "Type" : undefined} aria-label="Dependency type" data={DEP_TYPES} allowDeselect={false} {...form.getInputProps(`deps.${i}.type`)} />
                  <TextInput style={{ flex: 1 }} label={i === 0 ? "Minimum" : undefined} aria-label="Minimum version" placeholder="1.0.0.0" {...form.getInputProps(`deps.${i}.min`)} />
                  <TextInput style={{ flex: 1 }} label={i === 0 ? "Maximum" : undefined} aria-label="Maximum version" placeholder="optional" {...form.getInputProps(`deps.${i}.max`)} />
                  <ActionIcon variant="subtle" color="red" mt={i === 0 ? 28 : 4} aria-label="Remove dependency" onClick={() => form.removeListItem("deps", i)}>
                    <IconTrash size={16} />
                  </ActionIcon>
                </Group>
              ))}
            </Stack>
          ) : (
            <Text size="sm">None. Add one when this revision needs another file set at a minimum version (sent as <Code>DependsOnFileSetId</Code>).</Text>
          )}
        </Section>
        <UploadProgress pct={pct} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={m.isPending}>Cancel</Button>
          <Button type="submit" leftSection={<IconUpload size={16} />} loading={m.isPending}>Create revision</Button>
        </Group>
      </Stack>
    </form>
  );
}
