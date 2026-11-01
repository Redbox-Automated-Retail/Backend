import { Button } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { IconRocket, IconTerminal2 } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { dayjs, num } from "@/lib/format";
import { PageHeader, StatCards } from "@/components/ui";
import { ErrorState } from "@/components/DataTable";

type Fleet = {
  status: { online: number; recent: number; stale: number; offline: number; total: number; mqtt: number };
  counts: { rentals_today: number; rentals_out: number; discs: number; reservations_open: number; returns_24h: number; broker_kiosks: number };
};

export function OverviewPage() {
  const can = useCan();
  const q = useQuery({ queryKey: ["fleet"], queryFn: () => api<Fleet>("/fleet"), refetchInterval: 60_000 });
  const s = q.data?.status, c = q.data?.counts;
  const up = s ? s.online + s.recent : 0;
  return (
    <>
      <PageHeader title="Overview"
        description={s ? `${num(up)} of ${num(s.total)} kiosks online · ${dayjs().format("dddd, MMMM D, h:mm A")}` : dayjs().format("dddd, MMMM D, h:mm A")}
        actions={can() && <>
          <Button component={Link} to="/commands" variant="default" leftSection={<IconTerminal2 size={16} />}>Send a command</Button>
          <Button component={Link} to="/releases" leftSection={<IconRocket size={16} />}>Roll out a release</Button>
        </>} />
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <StatCards loading={q.isPending} stats={[
          { label: "Kiosks online", value: <>{num(up)}<span style={{ fontSize: 15, fontWeight: 700 }}> / {num(s?.total)}</span></>, to: "/kiosks",
            hint: s && ([s.stale && `${num(s.stale)} stale`, s.offline && `${num(s.offline)} offline`].filter(Boolean).join(" · ") || "all reporting") },
          { label: "Rentals today", value: num(c?.rentals_today), hint: `${num(c?.returns_24h)} returns in the last 24 h`, to: "/rentals" },
          { label: "Discs out", value: num(c?.rentals_out), hint: `${num(c?.discs)} discs in kiosks`, to: "/rentals?status=out" },
          { label: "Open reservations", value: num(c?.reservations_open), hint: `${num(c?.broker_kiosks)} kiosks on the broker`, to: "/reservations" },
        ]} />
      )}
    </>
  );
}
