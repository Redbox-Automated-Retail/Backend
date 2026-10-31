// The kiosk catalog (profile.data + inventory.data): titles, product groups, barcodes, the other tables, and publishing
// it to kiosks as a dvd-data revision. Tabs are route segments: /catalog/titles (default), groups, barcodes, tables, publish.
import { Link, useParams } from "react-router-dom";
import { Button } from "@mantine/core";
import { IconRocket } from "@tabler/icons-react";
import { PageHeader, RouteTabs } from "@/components/ui";
import { useCan } from "@/lib/auth";
import { num } from "@/lib/format";
import { useCatalogStatus } from "./shared";
import { TitlesTab } from "./TitlesTab";
import { GroupsTab } from "./GroupsTab";
import { BarcodesTab } from "./BarcodesTab";
import { TablesTab } from "./TablesTab";
import { PublishTab } from "./PublishTab";

const TABS = ["titles", "groups", "barcodes", "tables", "publish"] as const;
type Tab = (typeof TABS)[number];

export function CatalogPage() {
  const { tab: raw } = useParams();
  const tab: Tab = (TABS as readonly string[]).includes(raw || "") ? (raw as Tab) : "titles";
  const can = useCan()("change");
  const s = useCatalogStatus();
  const titles = s.data?.tables.find((t) => t.name === "ProductCatalog")?.rows;
  const groups = s.data?.tables.find((t) => t.name === "ProductGroup")?.rows;
  const st = s.data?.state;
  const dirty = !!(st?.changed_at && (!st.stamped_at || st.changed_at > st.stamped_at));
  const description = s.data
    ? `${num(titles)} titles · ${num(s.data.images)} box-art images${dirty ? " · changes not yet published" : ""}`
    : "The titles, box art and barcodes the kiosks show, published to them as a dvd-data revision";
  return (
    <>
      <PageHeader title="Catalog" description={description}
        actions={can && tab !== "publish" ? <Button component={Link} to="/catalog/publish" leftSection={<IconRocket size={16} />}>Build &amp; publish</Button> : undefined} />
      <RouteTabs base="/catalog" value={tab} tabs={[
        { value: "titles", label: "Titles", count: titles != null ? num(titles) : undefined },
        { value: "groups", label: "Product groups", count: groups != null ? num(groups) : undefined },
        { value: "barcodes", label: "Barcodes" },
        { value: "tables", label: "Other tables" },
        { value: "publish", label: "Publish", count: dirty ? "changes" : undefined },
      ]} />
      {tab === "titles" && <TitlesTab />}
      {tab === "groups" && <GroupsTab />}
      {tab === "barcodes" && <BarcodesTab />}
      {tab === "tables" && <TablesTab />}
      {tab === "publish" && <PublishTab />}
    </>
  );
}
