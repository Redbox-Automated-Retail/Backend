// Build profile.data + inventory.data and publish them as a dvd-data revision to every kiosk, one market or one kiosk.
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Anchor, Button, Code, Drawer, Grid, SegmentedControl, Select, Skeleton, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconRocket } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { ago, dateTime, num, short } from "@/lib/format";
import { ErrorState, LocalTable } from "@/components/DataTable";
import { Facts, Section, Status, useUrlParam } from "@/components/ui";
import { JobProgress } from "@/components/JobProgress";
import { jobKind, useCatalogStatus, type Job } from "./shared";

const schema = z.object({ scope: z.enum(["all", "market", "store"]), target: z.string(), label: z.string() })
  .refine((v) => v.scope === "all" || v.target.trim(), { path: ["target"], message: "Pick the market or kiosk" });
type Values = z.infer<typeof schema>;

export function PublishTab() {
  const can = useCan()("change");
  const s = useCatalogStatus(true);
  const [job, setJob] = useUrlParam("job");
  const [view, setView] = useUrlParam("log");
  const form = useForm<Values>({ initialValues: { scope: "all", target: "", label: "" }, validate: zodResolver(schema) });
  const publish = useMutation({
    mutationFn: (v: Values) => api<{ job: number }>("/catalog/publish", { body: { scope: v.scope, target: v.scope === "all" ? null : v.target.trim(), label: v.label.trim() || null } }),
    onSuccess: (r) => { notifyOk(`Publishing (job ${r.job})`); setJob(String(r.job)); s.refetch(); },
  });
  const submit = form.onSubmit(async (v) => {
    const where = v.scope === "all" ? "every kiosk" : v.scope === "market" ? `the kiosks in market ${v.target}` : `kiosk ${v.target}`;
    if (await confirm({ title: "Build and publish the catalog?", confirmLabel: "Build and publish",
      body: `Builds profile.data and inventory.data from the catalog as it is now and publishes them to ${where}. They get it on their next Update Service poll.` })) publish.mutate(v);
  });

  if (s.isError) return <ErrorState error={s.error} onRetry={() => s.refetch()} />;
  const d = s.data;
  const st = d?.state;
  const dirty = !!(st?.changed_at && (!st.stamped_at || st.changed_at > st.stamped_at));
  const lb = st?.last_build;
  const viewJob = view ? d?.jobs.find((j) => String(j.id) === view) : null;
  return (
    <Stack gap="lg">
      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Section title="Catalog">
            {!d ? <Stack>{Array.from({ length: 4 }, (_, i) => <Skeleton key={i} h={36} />)}</Stack> : (
              <Stack gap="md">
                <Facts cols={2} items={[
                  ["Imported from", st?.source ? <Code>{st.source}</Code> : "Nothing imported yet"],
                  ["Imported", st?.imported_at ? `${dateTime(st.imported_at)} (${ago(st.imported_at)})` : "-"],
                  ["Last change", st?.changed_at ? <>{dateTime(st.changed_at)} {dirty ? <Status kind="warn">not built since</Status> : null}</> : "No edits"],
                  ["Last build", lb ? `${lb.revision ? short(lb.revision) + (lb.label ? " " + lb.label : "") : "no change"} for ${lb.scope}${lb.target ? " " + lb.target : ""}${lb.at ? ` (${dateTime(lb.at)})` : ""}` : "Never built"],
                  ["Box-art images", num(d.images)],
                  ["Barcodes", d.barcodes.map((b) => `${num(b.n)} ${b.source}`).join(", ") || "none"],
                ]} />
                <LocalTable rows={d.tables} rowKey={(t) => t.name} maxHeight={260} initialSort={{ sort: "ord", dir: "asc" }}
                  columns={[
                    { id: "ord", header: "#", cell: (t) => t.ord, value: (t) => t.ord, width: 50 },
                    { id: "name", header: "profile.data table", cell: (t) => t.name, value: (t) => t.name },
                    { id: "rows", header: "Rows", cell: (t) => num(t.rows), value: (t) => Number(t.rows), align: "right" },
                  ]} />
                <Text size="sm">To replace the catalog with a dvd-data revision, use the catalog import under <Anchor component={Link} to="/admin">System administration</Anchor>.</Text>
              </Stack>
            )}
          </Section>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Stack gap="lg">
            <Section title="Publish to kiosks">
              <Text size="sm" mb="md">
                Builds profile.data and inventory.data and adds them to the <Code>dvd-data</Code> repository
                (head {d?.repo?.head ? <Code>{short(d.repo.head)}</Code> : "none yet"}) as VCDIFF deltas. Kiosks get it on their next Update Service poll.
              </Text>
              <form onSubmit={submit}>
                <Stack gap="sm">
                  <SegmentedControl fullWidth disabled={!can} data={[{ value: "all", label: "Every kiosk" }, { value: "market", label: "One market" }, { value: "store", label: "One kiosk" }]}
                    value={form.values.scope} onChange={(v) => { form.setFieldValue("scope", v as Values["scope"]); form.setFieldValue("target", ""); }} />
                  {form.values.scope === "market" && (
                    <Select label="Market" searchable data={d?.markets || []} placeholder={d?.markets.length ? "Pick a market" : "No kiosk has a market yet"}
                      nothingFoundMessage="No such market" disabled={!can} {...form.getInputProps("target")} value={form.values.target || null} onChange={(v) => form.setFieldValue("target", v || "")} />
                  )}
                  {form.values.scope === "store" && <TextInput label="Kiosk" placeholder="Kiosk id (store number)" disabled={!can} {...form.getInputProps("target")} />}
                  <TextInput label="Label" description="Optional; names the dvd-data revision" placeholder="catalog-yyyymmdd-hhmm" disabled={!can} {...form.getInputProps("label")} />
                  {can && <Button type="submit" leftSection={<IconRocket size={16} />} loading={publish.isPending} mt="xs">Build and publish</Button>}
                  {!dirty && d && st?.stamped_at && <Text size="sm">Nothing changed since the last build; publishing again makes no new revision.</Text>}
                </Stack>
              </form>
            </Section>
            {job && (
              <Section title={`Publish job ${job}`} actions={<Button size="xs" variant="default" onClick={() => setJob(null)}>Hide</Button>}>
                <JobProgress jobId={Number(job)} onFinished={(j) => { s.refetch(); if (j.status === "done") notifyOk("Catalog published"); }} />
              </Section>
            )}
          </Stack>
        </Grid.Col>
      </Grid>
      <Section title="Catalog jobs" p={0}>
        <LocalTable<Job> rows={d?.jobs} loading={s.isPending} rowKey={(j) => j.id} onRowClick={(j) => setView(String(j.id))} initialSort={{ sort: "id", dir: "desc" }}
          empty={<Text size="sm" p="md">No catalog jobs yet. Imports and publishes show here.</Text>}
          columns={[
            { id: "id", header: "Job", cell: (j) => j.id, value: (j) => j.id, width: 70 },
            { id: "source", header: "What", cell: (j) => j.source, value: (j) => j.source },
            { id: "status", header: "Status", cell: (j) => <Status kind={jobKind(j.status)}>{j.status}</Status>, value: (j) => j.status },
            { id: "step", header: "Last step", cell: (j) => j.progress?.at(-1)?.m || "", width: 320 },
            { id: "started", header: "Started", cell: (j) => dateTime(j.started_at), value: (j) => j.started_at },
            { id: "finished", header: "Finished", cell: (j) => dateTime(j.finished_at), value: (j) => j.finished_at || "" },
          ]} />
      </Section>
      <Drawer opened={!!view} onClose={() => setView(null)} position="right" size="lg" title={<Text fw={800} size="lg">{viewJob ? viewJob.source : `Job ${view}`}</Text>}>
        {view && <JobProgress jobId={Number(view)} />}
      </Drawer>
    </Stack>
  );
}
