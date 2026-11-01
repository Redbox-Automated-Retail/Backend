import { lazy, Suspense, type ComponentType } from "react";
import { Route, Routes } from "react-router-dom";
import { Skeleton, Stack } from "@mantine/core";
import { AuthGate } from "./lib/auth";
import { Shell } from "./Shell";
import { NotFound } from "./pages/NotFound";

// one chunk per area, loaded when first opened
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const page = <T extends string>(load: () => Promise<Record<T, ComponentType<any>>>, name: T) =>
  lazy(() => load().then((m) => ({ default: m[name] })));

const Overview = page(() => import("./pages/overview/OverviewPage"), "OverviewPage");
const Kiosks = page(() => import("./pages/kiosks/KiosksPage"), "KiosksPage");
const Kiosk = page(() => import("./pages/kiosks/KioskPage"), "KioskPage");
const Stores = page(() => import("./pages/stores/StoresPage"), "StoresPage");
const SystemHealth = page(() => import("./pages/system/SystemHealthPage"), "SystemHealthPage");
const Rentals = page(() => import("./pages/rentals/RentalsPage"), "RentalsPage");
const Broker = page(() => import("./pages/broker/BrokerPage"), "BrokerPage");
const Catalog = page(() => import("./pages/catalog/CatalogPage"), "CatalogPage");
const Releases = page(() => import("./pages/releases/ReleasesPage"), "ReleasesPage");
const UpdateService = page(() => import("./pages/updates/UpdateServicePage"), "UpdateServicePage");
const FileSets = page(() => import("./pages/filesets/FileSetsPage"), "FileSetsPage");
const Config = page(() => import("./pages/config/ConfigPage"), "ConfigPage");
const Commands = page(() => import("./pages/commands/CommandsPage"), "CommandsPage");
const Provisioning = page(() => import("./pages/provisioning/ProvisioningPage"), "ProvisioningPage");
const Admin = page(() => import("./pages/admin/AdminPage"), "AdminPage");
const Access = page(() => import("./pages/access/AccessPage"), "AccessPage");
const Audit = page(() => import("./pages/audit/AuditPage"), "AuditPage");
const Logs = page(() => import("./pages/logs/LogsPage"), "LogsPage");
const Tables = page(() => import("./pages/tables/TablesPage"), "TablesPage");
const TableTabs = page(() => import("./pages/tables/TableTabsPage"), "TableTabsPage");

function Loading() {
  return <Stack gap="md"><Skeleton h={34} w={260} /><Skeleton h={18} w={420} /><Skeleton h={420} mt="md" /></Stack>;
}

export function App() {
  return (
    <AuthGate>
      <Shell>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/kiosks" element={<Kiosks />} />
            <Route path="/kiosks/:id/:tab?" element={<Kiosk />} />
            <Route path="/stores/:tab?" element={<Stores />} />
            <Route path="/system" element={<SystemHealth />} />
            <Route path="/rentals/:tab?" element={<Rentals />} />
            <Route path="/reservations" element={<TableTabs area="reservations" />} />
            <Route path="/broker" element={<Broker />} />
            <Route path="/customers/:tab?" element={<TableTabs area="customers" />} />
            <Route path="/pricing/:tab?" element={<TableTabs area="pricing" />} />
            <Route path="/tickets/:tab?" element={<TableTabs area="tickets" />} />
            <Route path="/catalog/:tab?" element={<Catalog />} />
            <Route path="/inventory" element={<TableTabs area="inventory" />} />
            <Route path="/releases/:repo?" element={<Releases />} />
            <Route path="/updates" element={<UpdateService />} />
            <Route path="/filesets" element={<FileSets />} />
            <Route path="/config/:tab?" element={<Config />} />
            <Route path="/commands" element={<Commands />} />
            <Route path="/provisioning/:tab?" element={<Provisioning />} />
            <Route path="/admin/:tab?" element={<Admin />} />
            <Route path="/access/:tab?" element={<Access />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/tables/:name?" element={<Tables />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </Shell>
    </AuthGate>
  );
}
