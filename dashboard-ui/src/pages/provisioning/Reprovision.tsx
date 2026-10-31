import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Autocomplete, Button, Code, Group, Modal, NumberInput, Stack, Text, Textarea, TextInput, type ButtonProps } from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconFileDownload } from "@tabler/icons-react";
import { api, qs } from "@/lib/api";

export type ProvisioningInfo = {
  backend_ip: string;
  host_entries: string[];
  service_hosts: Record<string, string>;
  markets: string[];
  installer_kiosks: any[];
  installer_sessions: any[];
  certificates: any[];
  registrations: any[];
};

export const useProvisioning = (enabled = true) =>
  useQuery({ queryKey: ["provisioning"], queryFn: () => api<ProvisioningInfo>("/provisioning"), enabled });

const schema = z.object({
  kiosk: z.number({ invalid_type_error: "Enter the kiosk's store ID" }).int().positive("Enter the kiosk's store ID"),
  address: z.string().trim().refine((v) => !v || /^[\w.:-]+$/.test(v), "An IP address or host name"),
  base_domain: z.string().trim().refine((v) => !v || /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(v), "A DNS domain such as kiosk.example.net"),
  market: z.string().trim(),
  rewrite: z.string().refine((v) => v.split("\n").map((x) => x.trim()).filter(Boolean).every((x) => /^\S+=\S+$/.test(x)), "Each line is OLD=NEW"),
});

function ReprovisionForm({ kioskId, market, onDone }: { kioskId?: number; market?: string; onDone: () => void }) {
  const info = useProvisioning();
  const form = useForm({
    initialValues: { kiosk: (kioskId ?? "") as number | "", address: "", base_domain: "", market: market || "", rewrite: "" },
    validate: zodResolver(schema),
  });
  const submit = form.onSubmit((v) => {
    const rewrite = v.rewrite.split("\n").map((x) => x.trim()).filter(Boolean);
    const url = `/api/reprovision/${encodeURIComponent(String(v.kiosk))}.json` +
      qs({ address: v.address.trim(), base_domain: v.base_domain.trim(), market: v.market.trim(), rewrite });
    window.location.href = url;
    onDone();
  });
  return (
    <form onSubmit={submit}>
      <Stack>
        <Text size="sm">
          Downloads the settings file for <Code>rb-reprovision apply</Code> (software/src/Tools/Reprovision): hosts entries, the backend CA,
          store ID, engine registry, and the Update Manager / Update Client / Kiosk Client / Device Service URLs.
        </Text>
        <NumberInput label="Kiosk (store) ID" required allowDecimal={false} allowNegative={false} hideControls data-autofocus={!kioskId} {...form.getInputProps("kiosk")} />
        <TextInput label="Backend address for the hosts file" description={`Empty: the backend's public IP${info.data?.backend_ip ? ` (${info.data.backend_ip})` : ""}`}
          placeholder={info.data?.backend_ip || "BACKEND_PUBLIC_IP"} {...form.getInputProps("address")} />
        <TextInput label="Or your own DNS domain" description="Kiosks then resolve the service names in this domain; no hosts entries are written"
          placeholder="kiosk.example.net" {...form.getInputProps("base_domain")} />
        <Autocomplete label="Market" description="Empty: the kiosk's market as this backend knows it" data={info.data?.markets || []} {...form.getInputProps("market")} />
        <Textarea label="URL rewrites" description="One OLD=NEW per line" autosize minRows={3} ff="monospace"
          placeholder="https://old-host=https://dataservice.kiosk.prod.redbox.com" {...form.getInputProps("rewrite")} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onDone}>Cancel</Button>
          <Button type="submit" leftSection={<IconFileDownload size={16} />}>Download</Button>
        </Group>
      </Stack>
    </form>
  );
}

/** A button that opens the reprovision-file dialog (GET /api/reprovision/{id}.json). */
export function ReprovisionButton({ kioskId, market, label = "Reprovision file...", ...button }: { kioskId?: number; market?: string; label?: string } & ButtonProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button leftSection={<IconFileDownload size={16} />} {...button} onClick={() => setOpen(true)}>{label}</Button>
      <Modal opened={open} onClose={() => setOpen(false)} title={<Text fw={800}>Reprovision a kiosk</Text>} size="lg">
        {open && <ReprovisionForm kioskId={kioskId} market={market} onDone={() => setOpen(false)} />}
      </Modal>
    </>
  );
}
