import { Drawer, Stack, Text } from "@mantine/core";
import { Facts } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { JsonBlock, KioskLink } from "./filters";

export type EventRow = { id: number; created_at: string; kiosk_id: number | null; source: string; kind: string; payload: unknown };

/** One event with its full payload. */
export function EventDrawer({ event, onClose }: { event: EventRow | null; onClose: () => void }) {
  return (
    <Drawer opened={!!event} onClose={onClose} position="right" size="lg"
      title={event && <Text fw={800}>{event.source} / {event.kind}</Text>}>
      {event && (
        <Stack>
          <Facts cols={2} items={[["Time", dateTime(event.created_at)], ["Kiosk", <KioskLink id={event.kiosk_id} />], ["Event id", event.id], ["Source", event.source]]} />
          <Text fw={700} size="sm">Payload</Text>
          <JsonBlock value={event.payload} mah={640} />
        </Stack>
      )}
    </Drawer>
  );
}
