// Small filter controls bound to URL parameters, shared by the logs, audit, rentals and broker pages.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Anchor, Code, ScrollArea, Select, TextInput, type ComboboxData } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconX } from "@tabler/icons-react";
import { DateField } from "@/components/fields";
import { useUrlParam } from "@/components/ui";
import { dayjs } from "@/lib/format";

/** A text box bound to a URL parameter (debounced). `digits` keeps only digits (kiosk ids). */
export function UrlTextInput({ param, label, placeholder, w = 140, digits }: { param: string; label?: string; placeholder?: string; w?: number | string; digits?: boolean }) {
  const [sp, setSp] = useSearchParams();
  const [value, setValue] = useState(sp.get(param) || "");
  const [debounced] = useDebouncedValue(value, 350);
  useEffect(() => { setValue(sp.get(param) || ""); }, [sp.get(param)]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if ((sp.get(param) || "") === debounced) return;
    setSp((p) => { const q = new URLSearchParams(p); if (debounced) q.set(param, debounced); else q.delete(param); return q; }, { replace: true });
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <TextInput label={label} placeholder={placeholder} w={w} value={value} aria-label={label || placeholder}
      inputMode={digits ? "numeric" : undefined}
      onChange={(e) => setValue(digits ? e.currentTarget.value.replace(/\D/g, "") : e.currentTarget.value)}
      rightSection={value ? <IconX size={14} style={{ cursor: "pointer" }} onClick={() => setValue("")} aria-label="Clear" /> : null} />
  );
}

/** A select bound to a URL parameter; clearing it removes the parameter. */
export function UrlSelect({ param, label, placeholder, data, w = 180 }: { param: string; label?: string; placeholder?: string; data: ComboboxData; w?: number | string }) {
  const [v, setV] = useUrlParam(param);
  return <Select label={label} placeholder={placeholder} data={data} value={v} onChange={setV} clearable searchable w={w} aria-label={label || placeholder} />;
}

/** From / to date pickers bound to ?since= and ?until= (ISO dates). */
export function UrlDateRange() {
  const [since, setSince] = useUrlParam("since");
  const [until, setUntil] = useUrlParam("until");
  return (
    <>
      <DateField label="From" value={since} onChange={setSince} w={170} maxDate={until ? dayjs(until).toDate() : undefined} />
      <DateField label="To" value={until} onChange={setUntil} w={170} minDate={since ? dayjs(since).toDate() : undefined} />
    </>
  );
}

/** The ?since=/?until= range as API parameters: the "to" day is included (until = the next midnight). */
export function rangeParams(since: string | null, until: string | null) {
  return { since: since || undefined, until: until ? dayjs(until).add(1, "day").format("YYYY-MM-DD") : undefined };
}

export function KioskLink({ id }: { id: number | string | null | undefined }) {
  if (id == null || id === "") return <>-</>;
  return <Anchor component={Link} to={`/kiosks/${id}`} fw={600} size="sm">{id}</Anchor>;
}

/** Pretty-printed JSON in a scrolling block. */
export function JsonBlock({ value, mah = 520 }: { value: unknown; mah?: number }) {
  return (
    <ScrollArea.Autosize mah={mah} type="auto">
      <Code block style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12.5 }}>
        {value === undefined ? "" : JSON.stringify(value, null, 2)}
      </Code>
    </ScrollArea.Autosize>
  );
}

/** A one-line JSON preview for table cells. */
export function jsonPreview(v: unknown, n = 160) {
  if (v == null) return "";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > n ? s.slice(0, n) + "..." : s;
}
