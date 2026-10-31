import { useParams } from "react-router-dom";
import { Text } from "@mantine/core";
import { PageHeader, RouteTabs } from "@/components/ui";
import { TableEditor } from "./TableEditor";

type Area = { title: string; description: string; base: string; tabs: [string, string][]; note?: string };
const AREAS: Record<string, Area> = {
  reservations: { title: "Reservations", base: "/reservations", tabs: [["reservations", "Reservations"]],
    description: "Discs held at a kiosk for pickup. The reservation broker page sends new ones to a kiosk." },
  customers: { title: "Customers", base: "/customers", description: "Loyalty accounts and what goes with them.",
    tabs: [["customers", "Customers"], ["cards", "Cards"], ["customer_offers", "Customer offers"], ["customer_subscriptions", "Subscriptions"], ["points_ledger", "Points"]] },
  pricing: { title: "Pricing & offers", base: "/pricing", description: "Prices and promotions the kiosks show.",
    note: "Kiosk Clients refresh pricing every 30 minutes to 26 hours and campaigns every 30 minutes; Remote commands can force a refresh.",
    tabs: [["pricing_default", "Default rental"], ["pricing_rental", "By price level"], ["pricing_purchase", "Purchase"], ["promo_codes", "Promo codes"],
      ["promo_redemptions", "Redemptions"], ["return_promo", "Return promo"], ["return_promo_issued", "Return codes issued"], ["customer_offers", "Customer offers"],
      ["campaigns", "Campaigns"], ["subscription_plans", "Subscriptions"], ["rbplus_campaigns", "Redbox+ campaigns"], ["ads", "Ads"]] },
  tickets: { title: "Event tickets", base: "/tickets", description: "The kiosk's ticket sales: events, productions, seats and orders.",
    tabs: [["ticket_events", "Events"], ["ticket_productions", "Productions"], ["ticket_groups", "Ticket groups"], ["ticket_group_prices", "Group prices"],
      ["ticket_price_codes", "Price codes"], ["ticket_orders", "Orders"], ["ticket_locks", "Seat locks"], ["ticket_delivery_methods", "Delivery"], ["ticket_notifications", "Notifications"]] },
  inventory: { title: "Inventory", base: "/inventory", tabs: [["inventory", "Discs"]], description: "The discs in each kiosk, as the kiosks report them. Filter by kiosk." },
};

/** Pages that are one or more generic tables (customers, pricing, tickets, ...), one tab per table. */
export function TableTabsPage({ area }: { area: keyof typeof AREAS | string }) {
  const a = AREAS[area];
  const { tab = a.tabs[0][0] } = useParams();
  return (
    <>
      <PageHeader title={a.title} description={a.description} />
      {a.note && <Text size="sm" mt={-8} mb="md">{a.note}</Text>}
      {a.tabs.length > 1 && <RouteTabs base={a.base} value={tab} tabs={a.tabs.map(([value, label]) => ({ value, label }))} />}
      <TableEditor key={tab} name={tab} />
    </>
  );
}
