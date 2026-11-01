import { Button, PasswordInput, Stack } from "@mantine/core";
import { useForm } from "@mantine/form";
import { modals } from "@mantine/modals";
import { zodResolver } from "mantine-form-zod-resolver";
import { useState } from "react";
import { z } from "zod";
import { api } from "@/lib/api";
import { notifyError, notifyOk } from "@/lib/notify";

const schema = z.object({
  current: z.string().min(1, "Enter your current password"),
  password: z.string().min(10, "At least 10 characters"),
  repeat: z.string(),
}).refine((v) => v.password === v.repeat, { path: ["repeat"], message: "The passwords differ" });

function OwnPasswordForm({ onDone }: { onDone: () => void }) {
  const form = useForm({ initialValues: { current: "", password: "", repeat: "" }, validate: zodResolver(schema) });
  const [busy, setBusy] = useState(false);
  return (
    <form onSubmit={form.onSubmit(async (v) => {
      setBusy(true);
      try {
        await api("/me/password", { body: { current: v.current, password: v.password } });
        notifyOk("Your password was changed");
        onDone();
      } catch (e) { notifyError(e); } finally { setBusy(false); }
    })}>
      <Stack>
        <PasswordInput label="Current password" autoComplete="current-password" data-autofocus {...form.getInputProps("current")} />
        <PasswordInput label="New password" description="At least 10 characters" autoComplete="new-password" {...form.getInputProps("password")} />
        <PasswordInput label="Repeat the new password" autoComplete="new-password" {...form.getInputProps("repeat")} />
        <Button type="submit" loading={busy}>Change password</Button>
      </Stack>
    </form>
  );
}

export function changeOwnPassword() {
  const id = modals.open({ title: "Change your password", children: <OwnPasswordForm onDone={() => modals.close(id)} /> });
}
