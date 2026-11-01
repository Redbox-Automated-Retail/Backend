import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { LocalTable } from "@/components/DataTable";
import { PageHeader } from "@/components/ui";
import { TableEditor } from "./TableEditor";

type T = { name: string; table: string; key: string[]; title: string };

/** Every editable table: the list, or one table's editor at /tables/{name}. */
export function TablesPage() {
  const { name } = useParams();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["tables", "list"], queryFn: () => api<T[]>("/tables") });
  const t = q.data?.find((x) => x.name === name);
  if (name) {
    return (
      <>
        <PageHeader title={t?.title || name} description={t ? `Database table ${t.table}` : undefined} crumbs={[{ label: "All tables", to: "/tables" }]} />
        <TableEditor key={name} name={name} />
      </>
    );
  }
  return (
    <>
      <PageHeader title="All tables" description="Every table the dashboard can edit. Changes are live: kiosks get them on their next call." />
      <LocalTable<T> rows={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => r.name}
        onRowClick={(r) => nav(`/tables/${r.name}`)} initialSort={{ sort: "title", dir: "asc" }}
        columns={[
          { id: "title", header: "Table", value: (r) => r.title, cell: (r) => r.title },
          { id: "table", header: "Database table", value: (r) => r.table, cell: (r) => r.table },
          { id: "key", header: "Key", cell: (r) => r.key.join(", ") },
        ]} />
    </>
  );
}
