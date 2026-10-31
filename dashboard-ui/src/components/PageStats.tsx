import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ago, num } from "@/lib/format";
import { StatCards, type Stat } from "./ui";

type S = Record<string, any>;
const plural = (n: number, one: string, many = one + "s") => `${num(n)} ${n === 1 ? one : many}`;

/** The stat cards at the top of each page (GET /api/stats/{area}): four numbers worth a glance. No money anywhere. */
const AREAS: Record<string, (s: S) => Stat[]> = {
  catalog: (s) => [
    { label: "Titles", value: num(s.titles), hint: `${num(s.games)} of them games`, to: "/catalog/titles" },
    { label: "Box art", value: num(s.images), hint: s.no_art ? `${num(s.no_art)} titles without` : "every title has some", to: s.no_art ? "/catalog/titles?show=noart" : undefined },
    { label: "Coming soon", value: num(s.coming_soon), hint: "release date still ahead", to: "/catalog/titles?show=soon" },
    { label: "Unpublished changes", value: num(s.unpublished), color: s.unpublished ? "yellow.4" : undefined,
      hint: s.last_build ? `last build ${ago(s.last_build)}` : "never built", to: "/catalog/publish" },
  ],
  releases: (s) => [
    { label: "Repositories", value: num(s.repositories), to: "/updates" },
    { label: "Unreleased revisions", value: num(s.unreleased), color: s.unreleased ? "yellow.4" : undefined, hint: "published, waiting for a rollout" },
    { label: "Store and market rollouts", value: num(s.rollouts), hint: "serving something other than the default" },
    { label: "Changes this week", value: num(s.changes_7d), hint: s.last_change ? `last ${ago(s.last_change)}` : "nothing rolled out yet" },
  ],
  updates: (s) => [
    { label: "Repositories", value: num(s.repositories), hint: `${num(s.revisions)} revisions` },
    { label: "Stores", value: num(s.stores), hint: "known to the Update Service" },
    { label: "Polling now", value: num(s.polling), color: s.polling ? "teal.4" : undefined, hint: "checked in during the last hour" },
    { label: "Editions", value: num(s.editions), hint: "imported data zips", to: "/admin/data-zips" },
  ],
  filesets: (s) => [
    { label: "File sets", value: num(s.filesets), hint: `${num(s.revisions)} revisions` },
    { label: "Deployments", value: num(s.deployments) },
    { label: "Kiosks reporting", value: num(s.kiosks), hint: s.in_progress ? `${num(s.in_progress)} installing now` : undefined },
    { label: "Problems", value: num(s.problems), color: s.problems ? "red.4" : "teal.4", hint: s.problems ? "error or missing dependency" : "none" },
  ],
  config: (s) => [
    { label: "Configuration version", value: s.version ? `v${s.version}` : "-", to: "/config/kiosk" },
    { label: "Values", value: num(s.config_values), hint: "default, market and kiosk" },
    { label: "Kiosks on the current version", value: <>{num(s.current)}<span style={{ fontSize: 15, fontWeight: 700 }}> / {num(s.kiosks)}</span></>,
      color: s.kiosks && s.current < s.kiosks ? "yellow.4" : undefined, hint: "of those that pulled it" },
    { label: "Engine config files", value: num(s.engine_files), to: "/config/engine" },
  ],
  rentals: (s) => [
    { label: "Rentals today", value: num(s.today), hint: `${num(s.week)} in the last 7 days` },
    { label: "Discs out", value: num(s.out), to: "/rentals/rentals?status=out" },
    { label: "Returns, 24 hours", value: num(s.returns_24h), to: "/rentals/returns" },
    { label: "All rentals", value: num(s.total) },
  ],
  reservations: (s) => [
    { label: "Open", value: num(s.open), color: s.open ? "teal.4" : undefined, hint: "discs held at a kiosk" },
    { label: "Picked up", value: num(s.picked_up) },
    { label: "Cancelled or expired", value: num(s.ended) },
    { label: "All reservations", value: num(s.total), to: "/broker" },
  ],
  customers: (s) => [
    { label: "Customers", value: num(s.customers) },
    { label: "Loyalty members", value: num(s.loyalty) },
    { label: "Cards", value: num(s.cards), to: "/customers/cards" },
    { label: "Open offers", value: num(s.offers), to: "/customers/customer_offers" },
  ],
  pricing: (s) => [
    { label: "Active promo codes", value: num(s.promo_codes), to: "/pricing/promo_codes" },
    { label: "Redemptions", value: num(s.redemptions), to: "/pricing/promo_redemptions" },
    { label: "Campaigns", value: num(s.campaigns), to: "/pricing/campaigns" },
    { label: "Unredeemed customer offers", value: num(s.offers), to: "/pricing/customer_offers" },
  ],
  tickets: (s) => [
    { label: "Events", value: num(s.events), to: "/tickets/ticket_events" },
    { label: "Productions", value: num(s.productions), to: "/tickets/ticket_productions" },
    { label: "Orders", value: num(s.orders), to: "/tickets/ticket_orders" },
    { label: "Seat locks", value: num(s.locks), to: "/tickets/ticket_locks" },
  ],
  inventory: (s) => [
    { label: "Discs in kiosks", value: num(s.discs) },
    { label: "Kiosks reporting", value: num(s.kiosks) },
    { label: "Discs out", value: num(s.out), to: "/rentals/rentals?status=out" },
    { label: "Last inventory sync", value: s.last_sync ? ago(s.last_sync) : "never" },
  ],
  provisioning: (s) => [
    { label: "Stores offered", value: num(s.offered), hint: "to the kiosk installer", to: "/stores/installer" },
    { label: "IoT certificates", value: num(s.certificates), hint: `${num(s.revoked)} revoked`, to: "/provisioning/certificates" },
    { label: "Update Client registrations", value: num(s.registrations), to: "/provisioning/certificates" },
    { label: "Installer sessions", value: num(s.installer_sessions), to: "/provisioning/installer" },
  ],
  access: (s) => [
    { label: "Dashboard users", value: num(s.users), hint: plural(s.admins, "admin"), to: "/access/users" },
    { label: "CI tokens", value: num(s.tokens), to: "/access/tokens" },
    { label: "Field-staff logins", value: num(s.staff), hint: s.staff ? undefined : "none: any login is accepted", to: "/access/staff" },
    { label: "Failed sign-ins, 24 hours", value: num(s.failed_logins), color: s.failed_logins ? "yellow.4" : undefined, to: "/audit?view=signins" },
  ],
  audit: (s) => [
    { label: "Changes, 24 hours", value: num(s.changes_24h) },
    { label: "People, 7 days", value: num(s.people_7d), hint: "who changed something" },
    { label: "Sign-ins, 24 hours", value: num(s.logins_24h) },
    { label: "Failed sign-ins, 24 hours", value: num(s.failed_logins), color: s.failed_logins ? "yellow.4" : undefined },
  ],
  logs: (s) => [
    { label: "Events, last hour", value: num(s.last_hour) },
    { label: "Events, 24 hours", value: num(s.last_day) },
    { label: "Kiosks heard from, 24 hours", value: num(s.kiosks_24h), to: "/kiosks" },
    { label: "All events", value: `~${num(s.total)}`, hint: "estimate" },
  ],
  admin: (s) => [
    { label: "Jobs running", value: num(s.running), color: s.running ? "yellow.4" : undefined, to: "/admin/jobs" },
    { label: "Failed jobs, 7 days", value: num(s.failed_7d), color: s.failed_7d ? "red.4" : undefined, to: "/admin/jobs" },
    { label: "Editions imported", value: num(s.editions), hint: s.seeds == null ? "no packages folder mounted" : `${plural(s.seeds, "seed")} in the packages folder`, to: "/admin/data-zips" },
    { label: "Catalog imported", value: s.catalog_imported ? ago(s.catalog_imported) : "never", to: "/admin/catalog" },
  ],
};

export function PageStats({ area }: { area: string }) {
  const q = useQuery({ queryKey: ["stats", area], queryFn: () => api<S>(`/stats/${area}`), refetchInterval: 60_000, meta: { silent: true } });
  const make = AREAS[area];
  if (!make || q.isError) return null;
  // placeholders while loading so the page doesn't jump
  return <StatCards loading={q.isPending} stats={q.data ? make(q.data) : make({}).map((x) => ({ ...x, value: "", hint: x.hint ? " " : undefined }))} />;
}
