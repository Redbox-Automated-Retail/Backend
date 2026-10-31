import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Anchor, Badge, Box, Card, Group, Paper, SimpleGrid, Skeleton, Stack, Tabs, Text, TextInput, Title, type MantineColor } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconSearch, IconX } from "@tabler/icons-react";

export function PageHeader({ title, description, actions, crumbs }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; crumbs?: { label: string; to: string }[] }) {
  useEffect(() => { if (typeof title === "string") document.title = `${title} - Redbox Control Center`; }, [title]);
  return (
    <Box mb="lg">
      {crumbs && (
        <Group gap={6} mb={6}>
          {crumbs.map((c) => <Anchor key={c.to} component={Link} to={c.to} size="sm" fw={600} c="dark.1">{c.label} /</Anchor>)}
        </Group>
      )}
      <Group justify="space-between" align="flex-end" gap="md">
        <div>
          <Title order={2} fz={26}>{title}</Title>
          {description && <Text mt={4} size="sm" fw={500} c="dark.1">{description}</Text>}
        </div>
        {actions && <Group gap="sm">{actions}</Group>}
      </Group>
    </Box>
  );
}

export type Stat = { label: string; value: ReactNode; hint?: ReactNode; to?: string; color?: MantineColor };
/** The stat tiles the overview uses; pages with numbers worth a glance show them the same way. */
export function StatCards({ stats, loading, cols = 4 }: { stats: Stat[]; loading?: boolean; cols?: number }) {
  return (
    <SimpleGrid cols={{ base: 1, xs: 2, md: cols }} spacing="md" mb="lg">
      {stats.map((s) => {
        const body = (
          <Card p="md" bg="dark.7" h="100%" style={s.to ? { transition: "border-color .15s" } : undefined}>
            <Text size="sm" fw={600}>{s.label}</Text>
            {loading ? <Skeleton h={30} w="45%" mt={8} /> : <Text fz={28} fw={800} mt={4} lh={1.2} c={s.color}>{s.value}</Text>}
            {s.hint && (loading ? <Skeleton h={12} w="70%" mt={8} /> : <Text size="sm" mt={4} c="dark.1">{s.hint}</Text>)}
          </Card>
        );
        return s.to ? <Anchor key={s.label} component={Link} to={s.to} underline="never" c="inherit">{body}</Anchor> : <div key={s.label}>{body}</div>;
      })}
    </SimpleGrid>
  );
}

/** A search box bound to a URL parameter (debounced), so searches can be linked and survive reloads. */
export function UrlSearch({ param = "q", placeholder, w }: { param?: string; placeholder: string; w?: number | string }) {
  const [sp, setSp] = useSearchParams();
  const [value, setValue] = useState(sp.get(param) || "");
  const [debounced] = useDebouncedValue(value, 300);
  useEffect(() => { setValue(sp.get(param) || ""); }, [sp.get(param)]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if ((sp.get(param) || "") === debounced) return;
    setSp((p) => { const q = new URLSearchParams(p); if (debounced) q.set(param, debounced); else q.delete(param); return q; }, { replace: true });
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <TextInput value={value} onChange={(e) => setValue(e.currentTarget.value)} placeholder={placeholder} leftSection={<IconSearch size={16} />}
      rightSection={value ? <IconX size={16} style={{ cursor: "pointer" }} onClick={() => setValue("")} aria-label="Clear search" /> : null}
      style={{ flex: 1 }} w={w} aria-label={placeholder}
      onKeyDown={(e) => { if (e.key === "Escape") setValue(""); }} />
  );
}

/** URL parameter state: [value, set] where set(undefined) removes it. */
export function useUrlParam(name: string): [string | null, (v: string | null | undefined) => void] {
  const [sp, setSp] = useSearchParams();
  const set = (v: string | null | undefined) =>
    setSp((p) => { const q = new URLSearchParams(p); if (v) q.set(name, v); else q.delete(name); return q; }, { replace: true });
  return [sp.get(name), set];
}

/** Tabs whose value is a route segment (e.g. /stores/banners). */
export function RouteTabs({ base, tabs, value }: { base: string; tabs: { value: string; label: ReactNode; count?: ReactNode }[]; value: string }) {
  const nav = useNavigate();
  return (
    <Tabs value={value} onChange={(v) => v && nav(`${base}/${v}`)} mb="md" keepMounted={false}>
      <Tabs.List>
        {tabs.map((t) => (
          <Tabs.Tab key={t.value} value={t.value} fw={600} rightSection={t.count != null ? <Badge size="sm" variant="light" color="gray">{t.count}</Badge> : undefined}>
            {t.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Tabs>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <Group gap="sm" mb="md" wrap="wrap" align="flex-end">{children}</Group>;
}

/** Status dot with a word: ok / warn / bad / off. */
export function Status({ kind, children }: { kind: "ok" | "warn" | "bad" | "off"; children: ReactNode }) {
  const c = { ok: "teal.4", warn: "yellow.4", bad: "red.4", off: "dark.1" }[kind];
  return <Group gap={6} wrap="nowrap" display="inline-flex"><Text c={c} fz={10} lh={1}>●</Text><Text span size="sm" fw={600} c={kind === "off" ? undefined : c}>{children}</Text></Group>;
}

export function Section({ title, actions, children, p = "md" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; p?: string | number }) {
  return (
    <Paper p={0} bg="dark.7" style={{ overflow: "hidden" }}>
      {(title || actions) && (
        <Group justify="space-between" px="md" py="sm" style={{ borderBottom: "1px solid var(--rb-line)" }}>
          {typeof title === "string" ? <Text fw={700}>{title}</Text> : title}
          {actions && <Group gap="xs">{actions}</Group>}
        </Group>
      )}
      <Box p={p}>{children}</Box>
    </Paper>
  );
}

/** Label / value pairs. */
export function Facts({ items, cols = 4 }: { items: [ReactNode, ReactNode][]; cols?: number }) {
  return (
    <SimpleGrid cols={{ base: 1, xs: 2, md: cols }} spacing="md">
      {items.map(([l, v], i) => (
        <Stack key={i} gap={2}><Text size="xs" fw={700} tt="uppercase" style={{ letterSpacing: ".06em" }} c="dark.1">{l}</Text><Text fw={600}>{v ?? "-"}</Text></Stack>
      ))}
    </SimpleGrid>
  );
}
