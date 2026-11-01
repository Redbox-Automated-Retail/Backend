import { useRef, useState } from "react";
import { ActionIcon, Combobox, Group, Loader, Text, TextInput, useCombobox } from "@mantine/core";
import { DatePickerInput, DateTimePicker, TimeInput, type DatePickerInputProps, type TimeInputProps } from "@mantine/dates";
import { useDebouncedValue } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { IconCalendar, IconClock, IconMapPin } from "@tabler/icons-react";
import { api, qs } from "@/lib/api";
import { dayjs } from "@/lib/format";

/** A date picker whose value is an ISO date string ("2026-01-31") or null; the server normalizes it. */
export function DateField({ value, onChange, ...rest }: Omit<DatePickerInputProps, "value" | "onChange"> & { value?: string | null; onChange: (v: string | null) => void }) {
  return (
    <DatePickerInput clearable valueFormat="MMM D, YYYY" leftSection={<IconCalendar size={16} />} placeholder="Pick a date"
      value={value ? dayjs(value).toDate() : null} onChange={(d) => onChange(d ? dayjs(d as Date).format("YYYY-MM-DD") : null)} {...(rest as object)} />
  );
}

/** Date and time; value is an ISO timestamp or null. */
export function DateTimeField({ value, onChange, label, description }: { value?: string | null; onChange: (v: string | null) => void; label?: string; description?: string }) {
  return (
    <DateTimePicker clearable label={label} description={description} valueFormat="MMM D, YYYY h:mm A" leftSection={<IconCalendar size={16} />}
      placeholder="Pick a date and time" value={value ? dayjs(value).toDate() : null} onChange={(d) => onChange(d ? dayjs(d).toISOString() : null)} />
  );
}

/** A time picker; value is "HH:mm" (24 h) or "". The clock button opens the browser's time picker. */
export function TimeField({ value, onChange, ...rest }: Omit<TimeInputProps, "value" | "onChange"> & { value?: string | null; onChange: (v: string) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <TimeInput ref={ref} {...rest} value={value || ""} onChange={(e) => onChange(e.currentTarget.value)}
      leftSection={<IconClock size={16} />}
      rightSection={<ActionIcon variant="subtle" color="gray" onClick={() => ref.current?.showPicker?.()} aria-label="Open time picker"><IconClock size={16} /></ActionIcon>} />
  );
}

export type Address = { label: string; address1?: string; city?: string; county?: string; state?: string; zip?: string; lat?: number; lon?: number; id?: string };

/** Street address search (OpenStreetMap, or Google Maps when the server has a key): picking one fills the rest. */
export function AddressSearch({ onPick, label = "Find an address", description }: { onPick: (a: Address) => void; label?: string; description?: string }) {
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() });
  const [q, setQ] = useState("");
  const [debounced] = useDebouncedValue(q, 350);
  const results = useQuery({
    queryKey: ["geocode", debounced],
    queryFn: () => api<{ provider: string; results: Address[] }>("/geocode/search" + qs({ q: debounced })),
    enabled: debounced.trim().length >= 3,
    staleTime: 300_000,
    meta: { silent: true },
  });
  const pick = async (a: Address) => {
    combobox.closeDropdown();
    setQ(a.label);
    onPick(a.id && !a.city ? await api<Address>("/geocode/details" + qs({ id: a.id })) : a);
  };
  const list = results.data?.results || [];
  return (
    <Combobox store={combobox} onOptionSubmit={(v) => pick(list[Number(v)])}>
      <Combobox.Target>
        <TextInput label={label} description={description} value={q} placeholder="Start typing a street address..."
          leftSection={<IconMapPin size={16} />} rightSection={results.isFetching ? <Loader size={16} /> : null}
          onChange={(e) => { setQ(e.currentTarget.value); combobox.openDropdown(); combobox.updateSelectedOptionIndex(); }}
          onFocus={() => combobox.openDropdown()} onBlur={() => combobox.closeDropdown()} />
      </Combobox.Target>
      <Combobox.Dropdown hidden={debounced.trim().length < 3}>
        <Combobox.Options>
          {results.isError ? <Combobox.Empty>{(results.error as Error).message}</Combobox.Empty>
            : results.isFetching && !list.length ? <Combobox.Empty>Searching...</Combobox.Empty>
            : !list.length ? <Combobox.Empty>No match</Combobox.Empty>
            : list.map((a, i) => (
              <Combobox.Option key={i} value={String(i)}>
                <Group gap={8} wrap="nowrap"><IconMapPin size={15} /><Text size="sm">{a.label}</Text></Group>
              </Combobox.Option>
            ))}
        </Combobox.Options>
        {results.data && <Combobox.Footer><Text size="xs">Search by {results.data.provider}</Text></Combobox.Footer>}
      </Combobox.Dropdown>
    </Combobox>
  );
}
