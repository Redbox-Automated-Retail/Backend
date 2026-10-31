import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type StoreValue = {
  store_id: number; address?: string; return_time?: string; rental_tax_rate?: number | null; purchase_tax_rate?: number | null;
  purchase_price?: number | null; vendor_id?: number | null; market_id?: number | null; banner_id?: number | null; sell_thru?: boolean;
  open_date?: string | null; inv_sync?: boolean; address1?: string | null; address2?: string | null; city?: string | null;
  county?: string | null; state?: string | null; zip?: string | null; collection_method?: number | null;
  digital_purchase_tax_rate?: number | null; service_fee?: number | null; service_fee_tax_rate?: number | null; return_only_mode_date?: string | null;
};
export type StoreRow = {
  store_id: number; store: StoreValue; banner: string | null; market: string | null; offered: boolean | null; due_time: string | null;
  kaseya_market_name: string | null; last_seen_at: string | null; edited_at: string | null;
  open_date_iso: string | null; return_only_mode_date_iso: string | null; return_time_24: string | null; due_time_24: string | null;
};
export type Named = { id: number; name: string; stores: number; edited_at: string | null };
export type Option = { id: number; name: string };
export type Lookups = { banners: Option[]; markets: Option[]; states: { code: string; name: string }[]; present: string[] };
export type StoreStats = { stores: number; banners: number; markets: number; offered: number; with_kiosk: number; unpublished: number; states: number; present: string[] };

export const useLookups = () => useQuery({ queryKey: ["stores", "lookups"], queryFn: () => api<Lookups>("/stores/lookups"), staleTime: 60_000 });
export const useStoreStats = () => useQuery({ queryKey: ["stores", "stats"], queryFn: () => api<StoreStats>("/stores/stats") });
