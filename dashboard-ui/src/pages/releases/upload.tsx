// Upload helpers shared by the Software pages (releases, update service, file sets):
// a multipart POST with upload progress (fetch has none) and a files-or-folder picker.
import { useRef } from "react";
import { Button, Group, Input, Progress, Stack, Switch, Text } from "@mantine/core";
import { IconFolder, IconFiles, IconX } from "@tabler/icons-react";
import { ApiError } from "@/lib/api";
import { bytes, num } from "@/lib/format";

/** POST a form to /api{path}, reporting upload progress (0..100); resolves with the JSON answer like api(). */
export function uploadForm<T = any>(path: string, form: FormData, onProgress?: (pct: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api" + path);
    x.withCredentials = true;
    x.setRequestHeader("X-Requested-With", "rb-admin");
    x.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100)); };
    x.onerror = () => reject(new ApiError(0, "The upload failed (network error)"));
    x.onload = () => {
      let data: any = null;
      try { data = x.responseText ? JSON.parse(x.responseText) : null; } catch { data = x.responseText; }
      if (x.status >= 200 && x.status < 300) return resolve(data as T);
      if (x.status === 401) window.dispatchEvent(new Event("rb:logged-out"));
      const msg = (data && (typeof data.detail === "string" ? data.detail : data.error)) || x.statusText || `HTTP ${x.status}`;
      reject(new ApiError(x.status, msg, data));
    };
    x.send(form);
  });
}

/** The name a picked file is uploaded under: its path inside the picked folder (without the folder), else its name. */
export const relName = (f: File) => {
  const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath;
  return rel ? rel.split("/").slice(1).join("/") : f.name;
};

/** Append files to a form under `field`, keeping sub-folders of a picked folder. */
export function appendFiles(fd: FormData, field: string, files: File[]) {
  for (const f of files) fd.append(field, f, relName(f));
}

/** Pick several files, or a whole folder (sub-folders kept). */
export function FilesPicker({ label, description, files, onChange, folder, onFolderChange, error, disabled }: {
  label: string; description?: string; files: File[]; onChange: (f: File[]) => void;
  folder: boolean; onFolderChange: (v: boolean) => void; error?: string; disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const total = files.reduce((a, f) => a + f.size, 0);
  const open = () => {
    const el = ref.current;
    if (!el) return;
    if (folder) el.setAttribute("webkitdirectory", ""); else el.removeAttribute("webkitdirectory");
    el.value = "";
    el.click();
  };
  return (
    <Input.Wrapper label={label} description={description} error={error} withAsterisk>
      <Stack gap={6} mt={4}>
        <Group gap="sm">
          <Button variant="default" onClick={open} disabled={disabled} leftSection={folder ? <IconFolder size={16} /> : <IconFiles size={16} />}>
            {folder ? "Choose a folder..." : "Choose files..."}
          </Button>
          <Switch label="Pick a whole folder" checked={folder} disabled={disabled}
            onChange={(e) => { onFolderChange(e.currentTarget.checked); onChange([]); }} />
          {files.length > 0 && (
            <Button variant="subtle" color="gray" size="xs" leftSection={<IconX size={14} />} onClick={() => onChange([])} disabled={disabled}>Clear</Button>
          )}
        </Group>
        <Text size="sm" fw={600}>
          {files.length ? `${num(files.length)} ${files.length === 1 ? "file" : "files"}, ${bytes(total)}` : "Nothing chosen yet"}
        </Text>
        {files.length > 0 && (
          <Text size="xs" c="dark.1" lineClamp={2}>{files.slice(0, 8).map(relName).join(", ")}{files.length > 8 ? `, and ${num(files.length - 8)} more` : ""}</Text>
        )}
      </Stack>
      <input ref={ref} type="file" multiple hidden onChange={(e) => onChange(Array.from(e.currentTarget.files || []))} />
    </Input.Wrapper>
  );
}

/** Upload progress: percentage while sending, then "processing" while the server works on it. */
export function UploadProgress({ pct }: { pct: number | null }) {
  if (pct == null) return null;
  const sent = pct >= 100;
  return (
    <Stack gap={4}>
      <Group justify="space-between">
        <Text size="sm" fw={600}>{sent ? "Uploaded; the server is processing it..." : `Uploading... ${pct}%`}</Text>
      </Group>
      <Progress value={sent ? 100 : pct} animated={sent} striped={sent} size="md" aria-label="Upload progress" />
    </Stack>
  );
}
