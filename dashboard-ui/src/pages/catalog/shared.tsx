// Catalog (profile.data) shared pieces: API shapes, lookups, format pills, box art, background job progress.
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Job as SharedJob } from "@/components/JobProgress";

export type PType = { key: string; product_type_id: number; product_type_name: string; product_family_id: number };
export type Rating = { key: string; rating_id: number; name: string; product_family_id: number };
export type Genre = { key: string; genre_id: number; genre_name: string };
export type Family = { key: string; product_family_id: number; product_family_name?: string; genres?: Record<string, unknown> };
export type SubPlatform = { key: string; subplatform_id: number; subplatform_name: string };
export type Lookups = { types: PType[]; ratings: Rating[]; genres: Genre[]; families: Family[]; subplatforms: SubPlatform[] };

export type Job = SharedJob;
export type LastBuild = { revision?: string | null; label?: string | null; scope?: string; target?: string | null; at?: string };
export type CatalogStatus = {
  state: { source?: string | null; imported_at?: string | null; changed_at?: string | null; stamped_at?: string | null; last_build?: LastBuild | null } | null;
  tables: { name: string; ord: number; rows: number }[];
  images: number;
  barcodes: { source: string; n: number }[];
  repo: { name: string; serve_head?: string | null; head?: string | null } | null;
  markets: string[];
  jobs: Job[];
  omdb: boolean;
};

export type Group_ = { product_group_id: number; value: { product_group_name?: string; product_group_type_id?: number; product_id_list?: number[] } };
export type Barcode = { barcode: string; product_id?: number; status: number; rental_count?: number; source?: string; updated_at?: string | null };

export const GROUP_TYPES: { value: string; label: string }[] = [
  { value: "1", label: "1 - title rollup (DVD / Blu-ray / 4K of one title)" },
  { value: "3", label: "3 - multi-disc" },
  { value: "6", label: "6 - multi-disc workaround" },
];
export const BARCODE_STATUS: Record<number, string> = { 0: "Known", 1: "Wrong title", 2: "Damaged", 3: "Thinned", 6: "Fraud" };

export const useLookups = () =>
  useQuery({ queryKey: ["catalog", "lookups"], queryFn: () => api<Lookups>("/catalog/lookups"), staleTime: 300_000 });
export const useCatalogStatus = (poll = false) =>
  useQuery({
    queryKey: ["catalog", "status"], queryFn: () => api<CatalogStatus>("/catalog"),
    refetchInterval: (q) => (poll && q.state.data?.jobs.some((j) => j.status === "running") ? 3000 : false),
    meta: poll ? { silent: true } : undefined,
  });

/** product type -> family (1 movies, 2 games, 3 other / digital) */
export function typeInfo(L: Lookups | undefined, id: unknown): PType {
  return L?.types.find((t) => String(t.product_type_id) === String(id)) || { key: "", product_type_id: Number(id), product_type_name: "?", product_family_id: 1 };
}
export const isGameType = (L: Lookups | undefined, id: unknown) => Number(typeInfo(L, id).product_family_id) === 2;
export const ratingName = (L: Lookups | undefined, id: unknown) => L?.ratings.find((r) => String(r.rating_id) === String(id))?.name || "";

const nowStamp = () => new Date().toISOString().replace(/\D/g, "").slice(0, 14);
export const isSoon = (release?: string | null) => !!release && release > nowStamp();

export function FmtPill({ L, typeId, soon }: { L?: Lookups; typeId: unknown; soon?: boolean }) {
  if (soon) return <span className="fmt soon">Coming soon</span>;
  const t = typeInfo(L, typeId);
  const cls = isGameType(L, typeId) ? "game" : /blu|4k/i.test(t.product_type_name) ? "blu" : /digital/i.test(t.product_type_name) ? "digital" : "";
  return <span className={`fmt ${cls}`}>{t.product_type_name}</span>;
}

const ART_COLORS = ["#E42045,#380B44", "#F05C27,#8F0E15", "#792D8D,#060208", "#1D5FB8,#2F1E36", "#D92E6C,#481B54", "#555F64,#060208", "#2E7D5B,#12081A"];
export const imageUrl = (name?: string | null) => (name ? `/api/catalog/images/${encodeURIComponent(name)}` : "");

/** Box art over a coloured placeholder with the title; the image hides itself when it's missing. */
export function ArtBox({ L, row, big, src }: {
  L?: Lookups; big?: boolean; src?: string | null;
  row: { product_id?: number | null; long_name?: string | null; product_type_id?: unknown; release_date?: string | null; image_file?: string | null };
}) {
  const [broken, setBroken] = useState(false);
  const url = src || imageUrl(row.image_file);
  useEffect(() => setBroken(false), [url]);
  const c = ART_COLORS[Number(row.product_id || 0) % ART_COLORS.length];
  return (
    <div className="rb-art" style={{ background: `linear-gradient(160deg,${c})`, fontSize: big ? 20 : undefined }}>
      <FmtPill L={L} typeId={row.product_type_id} soon={isSoon(row.release_date)} />
      <span>{row.long_name}</span>
      {url && !broken && <img src={url} alt="" loading="lazy" onError={() => setBroken(true)} />}
    </div>
  );
}

export const jobKind = (s: Job["status"]) => (s === "done" ? "ok" : s === "failed" ? "bad" : "warn") as "ok" | "bad" | "warn";

/** "a,b, c" -> ["a","b","c"] */
export const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** The genres a family offers (ProductFamily.genres), plus any the title already has. */
export function familyGenres(L: Lookups, family: number, selected: string[]) {
  const famRow = L.families.find((f) => Number(f.product_family_id) === family);
  const allowed = new Set(Object.keys(famRow?.genres || {}));
  return L.genres.filter((g) => !allowed.size || allowed.has(String(g.genre_id)) || selected.includes(String(g.genre_id)));
}

