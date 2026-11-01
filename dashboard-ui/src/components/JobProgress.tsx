import { useEffect } from "react";
import { Alert, Code, Group, Loader, Progress, ScrollArea, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { IconCheck, IconX } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { dayjs } from "@/lib/format";

export type Job = { id: number; source: string; status: "running" | "done" | "failed"; progress: { t: string; m: string }[]; started_at: string; finished_at: string | null };

/** Follows a background job (GET /api/admin/jobs/{id}) until it ends: a running bar, its log, the outcome. */
export function JobProgress({ jobId, onFinished }: { jobId: number; onFinished?: (j: Job) => void }) {
  const q = useQuery({
    queryKey: ["admin", "job", jobId],
    queryFn: () => api<Job>(`/admin/jobs/${jobId}`),
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 1500 : false),
    meta: { silent: true },
  });
  const j = q.data;
  useEffect(() => { if (j && j.status !== "running") onFinished?.(j); }, [j?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!j) return <Group gap="sm"><Loader size="sm" /><Text size="sm">Starting...</Text></Group>;
  return (
    <Stack gap="sm">
      {j.status === "running" ? (
        <><Group gap="sm"><Loader size="sm" /><Text size="sm" fw={600}>Working: {j.progress.at(-1)?.m || "starting"}</Text></Group>
          <Progress value={100} animated striped size="lg" aria-label="Job running" /></>
      ) : j.status === "done" ? (
        <Alert color="teal" icon={<IconCheck size={18} />} title="Done">{j.progress.at(-1)?.m}</Alert>
      ) : (
        <Alert color="red" icon={<IconX size={18} />} title="Failed">{j.progress.at(-1)?.m}</Alert>
      )}
      <ScrollArea.Autosize mah={220} type="auto">
        <Code block>{j.progress.map((p) => `${dayjs(p.t).format("HH:mm:ss")}  ${p.m}`).join("\n") || "..."}</Code>
      </ScrollArea.Autosize>
    </Stack>
  );
}
