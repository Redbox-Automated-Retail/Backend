import { useEffect, useState } from "react";
import { Button, Code, Group, JsonInput, Modal, NumberInput, ScrollArea, Select, Stack, Text } from "@mantine/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { notifyOk } from "@/lib/notify";

type Templates = Record<string, { version: number; payload: unknown }>;

/** One IoT command (Update Client) to one or more kiosks; templates fill version and payload. */
export function CommandDialog({ kiosks, preset, opened, onClose }: { kiosks: (number | string)[]; preset?: string; opened: boolean; onClose: () => void }) {
  const templates = useQuery({ queryKey: ["jobs", "commands"], queryFn: () => api<Templates>("/jobs/commands"), staleTime: Infinity, enabled: opened });
  const [command, setCommand] = useState<string | null>(preset || "Ping");
  const [version, setVersion] = useState<number | string>(2);
  const [payload, setPayload] = useState("");
  useEffect(() => {
    const t = command && templates.data?.[command];
    if (t) { setVersion(t.version); setPayload(t.payload == null ? "" : JSON.stringify(t.payload, null, 2)); }
  }, [command, templates.data]);
  useEffect(() => { if (opened) setCommand(preset || "Ping"); }, [opened, preset]);
  const send = useMutation({
    mutationFn: () => api<{ delivered: number; results: unknown[] }>("/kiosks/command", { body: { kiosks, command, version, payload } }),
    onSuccess: (r) => notifyOk(`${command}: delivered to ${r.delivered} of ${kiosks.length} kiosk${kiosks.length === 1 ? "" : "s"}`),
  });
  return (
    <Modal opened={opened} onClose={() => { send.reset(); onClose(); }} size="lg"
      title={<Text fw={800}>Command to {kiosks.length === 1 ? `kiosk ${kiosks[0]}` : `${kiosks.length} kiosks`}</Text>}>
      <Stack>
        <Select label="Command" searchable data={Object.keys(templates.data || {})} value={command} onChange={setCommand} disabled={templates.isPending} />
        <NumberInput label="Version" value={version} onChange={setVersion} w={140} min={1} />
        <JsonInput label="Payload (JSON)" autosize minRows={3} maxRows={14} value={payload} onChange={setPayload} formatOnBlur validationError="Not valid JSON" />
        <Text size="sm">Only kiosks whose Update Client is connected over MQTT get it now.</Text>
        {send.data && <ScrollArea.Autosize mah={200}><Code block>{JSON.stringify(send.data.results, null, 2)}</Code></ScrollArea.Autosize>}
        <Group justify="flex-end"><Button variant="default" onClick={onClose}>Close</Button>
          <Button onClick={() => send.mutate()} loading={send.isPending} disabled={!command}>Send</Button></Group>
      </Stack>
    </Modal>
  );
}
