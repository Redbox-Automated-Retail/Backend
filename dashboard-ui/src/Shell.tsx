import { useEffect, type ReactNode } from "react";
import { NavLink as RouterLink, useLocation } from "react-router-dom";
import { ActionIcon, AppShell, Avatar, Burger, Group, Menu, NavLink, ScrollArea, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { nprogress } from "@mantine/nprogress";
import { useIsFetching, useIsMutating, useQuery } from "@tanstack/react-query";
import {
  IconAdjustments, IconBuildingStore, IconChecklist, IconDatabase, IconDeviceDesktop, IconDisc, IconFolders,
  IconGift, IconHeartbeat, IconHome, IconKey, IconLayersSubtract, IconLink, IconListDetails, IconLogout, IconMovie, IconPassword,
  IconRocket, IconServerCog, IconShieldLock, IconShoppingCart, IconTerminal2, IconTicket, IconTool, IconUsers,
} from "@tabler/icons-react";
import { api } from "./lib/api";
import { useCan, useMe } from "./lib/auth";
import { changeOwnPassword } from "./pages/access/ownPassword";

type Item = { to: string; label: string; icon: typeof IconHome; admin?: boolean };
export const NAV: [string, Item[]][] = [
  ["Control center", [
    { to: "/", label: "Overview", icon: IconHome },
    { to: "/kiosks", label: "Kiosks", icon: IconDeviceDesktop },
    { to: "/stores", label: "Stores", icon: IconBuildingStore },
    { to: "/system", label: "System health", icon: IconHeartbeat },
  ]],
  ["Kiosk activity", [
    { to: "/rentals", label: "Rentals & returns", icon: IconShoppingCart },
    { to: "/reservations", label: "Reservations", icon: IconTicket },
    { to: "/broker", label: "Reservation broker", icon: IconLink },
    { to: "/customers", label: "Customers", icon: IconUsers },
    { to: "/pricing", label: "Pricing & offers", icon: IconGift },
    { to: "/tickets", label: "Event tickets", icon: IconTicket },
  ]],
  ["Content", [
    { to: "/catalog", label: "Catalog", icon: IconMovie },
    { to: "/inventory", label: "Inventory", icon: IconDisc },
  ]],
  ["Software", [
    { to: "/releases", label: "Releases", icon: IconRocket },
    { to: "/updates", label: "Update Service", icon: IconLayersSubtract },
    { to: "/filesets", label: "File sets", icon: IconFolders },
    { to: "/config", label: "Configuration", icon: IconAdjustments },
  ]],
  ["Operations", [
    { to: "/commands", label: "Remote commands", icon: IconTerminal2 },
    { to: "/provisioning", label: "Provisioning", icon: IconTool },
  ]],
  ["Administration", [
    { to: "/admin", label: "System administration", icon: IconServerCog },
    { to: "/access", label: "Users & access", icon: IconShieldLock },
    { to: "/audit", label: "Audit log", icon: IconChecklist },
    { to: "/logs", label: "Logs & events", icon: IconListDetails },
    { to: "/tables", label: "All tables", icon: IconDatabase },
  ]],
];

/** The thin top bar runs while anything loads or saves (queries marked meta.silent, like the health poll, don't count). */
function GlobalProgress() {
  const fetching = useIsFetching({ predicate: (q) => !q.meta?.silent });
  const mutating = useIsMutating();
  useEffect(() => {
    if (fetching + mutating > 0) nprogress.start();
    else nprogress.complete();
  }, [fetching, mutating]);
  return null;
}

function Health() {
  const q = useQuery({ queryKey: ["system"], queryFn: () => api("/system"), refetchInterval: 30_000, meta: { silent: true } });
  const services: { ok: boolean }[] = q.data?.services || [];
  const up = services.filter((s) => s.ok).length;
  const color = !q.data ? "gray" : up === services.length ? "teal.4" : up >= services.length - 1 ? "yellow.4" : "red.5";
  const label = q.isError ? "Health unknown" : !q.data ? "Checking services..." : up === services.length ? "All services healthy" : `${up} of ${services.length} services healthy`;
  return (
    <UnstyledButton component={RouterLink} to="/system" px="sm" py={6}>
      <Group gap={8} wrap="nowrap">
        <Text c={color} fz={11} lh={1}>●</Text>
        <Text size="sm" fw={600} c="dark.0">{label}</Text>
      </Group>
    </UnstyledButton>
  );
}

function UserMenu() {
  const me = useMe();
  const logout = async () => {
    await api("/logout", { body: {} }).catch(() => undefined);
    window.dispatchEvent(new Event("rb:logged-out"));
  };
  return (
    <Group justify="space-between" px="sm" py="sm" style={{ borderTop: "1px solid var(--rb-line)" }} wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        <Avatar color="rubine" radius="md" size={32}>{me.user.slice(0, 1).toUpperCase()}</Avatar>
        <div>
          <Text size="sm" fw={700} lh={1.2}>{me.user}</Text>
          <Text size="xs" tt="capitalize" c="dark.1">{me.role}</Text>
        </div>
      </Group>
      <Menu position="top-end" withArrow>
        <Menu.Target>
          <Tooltip label="Account"><ActionIcon variant="subtle" color="gray" aria-label="Account menu"><IconKey size={18} /></ActionIcon></Tooltip>
        </Menu.Target>
        <Menu.Dropdown>
          {!me.bootstrap && <Menu.Item leftSection={<IconPassword size={16} />} onClick={changeOwnPassword}>Change password</Menu.Item>}
          <Menu.Item leftSection={<IconLogout size={16} />} onClick={logout}>Sign out</Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [opened, { toggle, close }] = useDisclosure();
  const loc = useLocation();
  const can = useCan();
  useEffect(close, [loc.pathname, close]);
  const active = (to: string) => (to === "/" ? loc.pathname === "/" : loc.pathname === to || loc.pathname.startsWith(to + "/"));
  return (
    <AppShell navbar={{ width: 236, breakpoint: "sm", collapsed: { mobile: !opened } }} header={{ height: { base: 52, sm: 0 } }} padding={0}>
      <GlobalProgress />
      <AppShell.Header hiddenFrom="sm" className="rb-sidebar">
        <Group h="100%" px="md"><Burger opened={opened} onClick={toggle} size="sm" aria-label="Menu" />
          <div className="rb-logo"><span className="mark">r.</span><span className="word">redbox<i>.</i></span></div></Group>
      </AppShell.Header>
      <AppShell.Navbar className="rb-sidebar">
        <AppShell.Section p="md" pb={4} visibleFrom="sm">
          <div className="rb-logo"><span className="mark">r.</span><span className="word">redbox<i>.</i></span></div>
        </AppShell.Section>
        <AppShell.Section grow component={ScrollArea} px="xs" className="rb-nav">
          {NAV.map(([group, items]) => (
            <div key={group}>
              <div className="rb-nav-group">{group}</div>
              {items.filter((i) => !i.admin || can("admin")).map((i) => (
                <NavLink key={i.to} component={RouterLink} to={i.to} label={i.label} active={active(i.to)}
                  leftSection={<i.icon size={18} stroke={1.8} />} py={7} />
              ))}
            </div>
          ))}
        </AppShell.Section>
        <AppShell.Section pt="xs"><Health /><UserMenu /></AppShell.Section>
      </AppShell.Navbar>
      <AppShell.Main className="rb-dots">
        <div className="rb-main" style={{ padding: "28px 32px 64px", maxWidth: 1500 }}>{children}</div>
      </AppShell.Main>
    </AppShell>
  );
}
