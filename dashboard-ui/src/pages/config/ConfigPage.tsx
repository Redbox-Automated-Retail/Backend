import { useParams } from "react-router-dom";
import { PageHeader, RouteTabs } from "@/components/ui";
import { KioskConfig } from "./KioskConfig";
import { EngineConfig } from "./EngineConfig";

export function ConfigPage() {
  const tab = useParams().tab === "engine" ? "engine" : "kiosk";
  return (
    <>
      <PageHeader stats="config" title="Configuration" description="Kiosk configuration values (Update Client) and the engine config files updatemgrd delivers." />
      <RouteTabs base="/config" value={tab} tabs={[
        { value: "kiosk", label: "Kiosk configuration (Update Client)" },
        { value: "engine", label: "Engine config files (updatemgrd)" },
      ]} />
      {tab === "kiosk" ? <KioskConfig /> : <EngineConfig />}
    </>
  );
}
