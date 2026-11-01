import { notifications } from "@mantine/notifications";
import { modals } from "@mantine/modals";
import { Text } from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";
import type { ReactNode } from "react";

export const notifyOk = (message: ReactNode, title?: string) =>
  notifications.show({ color: "teal", icon: <IconCheck size={18} />, title, message, autoClose: 4000 });

export const notifyError = (e: unknown, title = "That didn't work") =>
  notifications.show({ color: "red", icon: <IconX size={18} />, title, message: e instanceof Error ? e.message : String(e), autoClose: 9000 });

/** A confirm dialog that resolves true / false. */
export function confirm(opts: { title: string; body: ReactNode; confirmLabel?: string; danger?: boolean }): Promise<boolean> {
  return new Promise((resolve) => {
    modals.openConfirmModal({
      title: <Text fw={800}>{opts.title}</Text>,
      children: typeof opts.body === "string" ? <Text size="sm">{opts.body}</Text> : opts.body,
      labels: { confirm: opts.confirmLabel || "Confirm", cancel: "Cancel" },
      confirmProps: { color: opts.danger ? "red" : "rubine" },
      onConfirm: () => resolve(true),
      onCancel: () => resolve(false),
      onClose: () => resolve(false),
    });
  });
}
