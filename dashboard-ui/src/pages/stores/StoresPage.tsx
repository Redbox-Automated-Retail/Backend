import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Alert, Anchor, Button, Text } from "@mantine/core";
import { IconPlus, IconInfoCircle } from "@tabler/icons-react";
import { useCan } from "@/lib/auth";
import { num } from "@/lib/format";
import { PageHeader, RouteTabs, StatCards } from "@/components/ui";
import { useStoreStats } from "./api";
import { StoresTab } from "./StoresTab";
import { NamedTab } from "./NamedTab";
import { InstallerTab } from "./InstallerTab";

/** Store management: stores are physical locations (profile.data Store / Banner / Market); a kiosk is the machine set
 *  up as one. Changes go to kiosks with the next catalog build. */
export function StoresPage() {
  const { tab = "stores" } = useParams();
  const can = useCan();
  const stats = useStoreStats();
  const s = stats.data;
  const [creating, setCreating] = useState<null | "stores" | "banners" | "markets">(null);
  const missing = s ? ["Store", "Banner", "Market"].filter((t) => !s.present.includes(t)) : [];
  const addLabel = { stores: "Add a store", banners: "Add a banner", markets: "Add a market" }[tab as "stores"];
  return (
    <>
      <PageHeader title="Stores" description="Physical locations a kiosk can be set up as: stores, the retailers (banners) they belong to and their markets."
        actions={can() && addLabel && <Button leftSection={<IconPlus size={16} />} onClick={() => setCreating(tab as "stores")}>{addLabel}</Button>} />
      <StatCards loading={stats.isPending} cols={4} stats={[
        { label: "Stores", value: num(s?.stores), hint: `in ${num(s?.states)} states`, to: "/stores/stores" },
        { label: "Banners and markets", value: <>{num(s?.banners)}<span style={{ fontSize: 15, fontWeight: 700 }}> · {num(s?.markets)}</span></>, hint: "retailers · markets", to: "/stores/banners" },
        { label: "Offered to the kiosk installer", value: num(s?.offered), hint: `${num(s?.with_kiosk)} stores have a kiosk set up`, to: "/stores/installer" },
        { label: "Unpublished changes", value: num(s?.unpublished), color: s?.unpublished ? "yellow.4" : undefined,
          hint: s?.unpublished ? "go to kiosks with the next catalog build" : "kiosks have every change", to: s?.unpublished ? "/catalog/publish" : undefined },
      ]} />
      {missing.length > 0 && (
        <Alert color="yellow" icon={<IconInfoCircle size={18} />} mb="md" title={`No ${missing.join(", ")} table yet`}>
          <Text size="sm">Import the stores from seed data on <Anchor component={Link} to="/admin/stores">System administration</Anchor>, or import the whole catalog.</Text>
        </Alert>
      )}
      <RouteTabs base="/stores" value={tab} tabs={[
        { value: "stores", label: "Stores" }, { value: "banners", label: "Banners" }, { value: "markets", label: "Markets" },
        { value: "installer", label: "Kiosk installer" },
      ]} />
      {tab === "stores" && <StoresTab creating={creating === "stores"} onCreated={() => setCreating(null)} />}
      {(tab === "banners" || tab === "markets") && <NamedTab kind={tab} creating={creating === tab} onCreated={() => setCreating(null)} />}
      {tab === "installer" && <InstallerTab />}
    </>
  );
}
