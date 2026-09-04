import { ADMIN } from "@/lib/admin-copy";

import { adminCall, fmtDate, type TenantSummary } from "./_server";

const STATUSES = ["", "active", "trial", "suspended"] as const;
const copy = ADMIN.tenants;

/** Phase 18.1 — every tenant, searchable, with its size at a glance. */
export default async function AdminTenants({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; status?: string }>;
}) {
  const { q = "", status = "" } = await searchParams;
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (status) params.set("status_filter", status);
  const query = params.toString();
  const tenants = await adminCall<TenantSummary[]>(`/api/v1/platform/tenants${query ? `?${query}` : ""}`);

  const count = (s: string) => tenants.filter((t) => t.status === s).length;
  const openTickets = tenants.reduce((sum, t) => sum + t.open_tickets, 0);
  const members = tenants.reduce((sum, t) => sum + t.members, 0);

  return (
    <>
      <div className="pa-head">
        <div>
          <h1>{copy.title}</h1>
          <p>{copy.intro}</p>
        </div>
      </div>

      <div className="pa-metrics" aria-label={copy.summary}>
        <div className="pa-metric">
          <div className="pa-metric-label">{copy.total}</div>
          <div className="pa-metric-value">{tenants.length}</div>
        </div>
        <div className="pa-metric">
          <div className="pa-metric-label">{copy.active}</div>
          <div className="pa-metric-value">{count("active")}<small>{copy.trialShort} {count("trial")}</small></div>
        </div>
        <div className="pa-metric">
          <div className="pa-metric-label">{copy.suspended}</div>
          <div className="pa-metric-value">{count("suspended")}</div>
        </div>
        <div className="pa-metric">
          <div className="pa-metric-label">{copy.members}</div>
          <div className="pa-metric-value">{members}</div>
        </div>
        <div className="pa-metric">
          <div className="pa-metric-label">{copy.openTickets}</div>
          <div className="pa-metric-value">{openTickets}</div>
        </div>
      </div>

      <form className="pa-filters" method="get" action="/admin">
        <label className="pa-field">
          {copy.search}
          <input name="q" defaultValue={q} placeholder={copy.searchPlaceholder} />
        </label>
        <label className="pa-field">
          {copy.statusLabel}
          <select name="status" defaultValue={status}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s ? ADMIN.status[s] : copy.anyStatus}</option>
            ))}
          </select>
        </label>
        <button type="submit" className="pa-btn pa-btn-primary">{copy.searchButton}</button>
        {(q || status) && <a className="pa-btn" href="/admin">{copy.clear}</a>}
      </form>

      <div className="pa-table-wrap">
        <table className="pa-table">
          <thead>
            <tr>
              <th>{copy.columns.shop}</th>
              <th>{copy.columns.status}</th>
              <th>{copy.columns.owner}</th>
              <th className="num">{copy.columns.members}</th>
              <th className="num">{copy.columns.customers}</th>
              <th className="num">{copy.columns.ticketsOpen}</th>
              <th className="num">{copy.columns.deals}</th>
              <th>{copy.columns.lastActivity}</th>
              <th>{copy.columns.created}</th>
            </tr>
          </thead>
          <tbody>
            {tenants.length === 0 && (
              <tr><td colSpan={9} className="pa-empty">{copy.empty}</td></tr>
            )}
            {tenants.map((t) => (
              <tr key={t.id}>
                <td>
                  <a className="row-link" href={`/admin/tenants/${t.id}`}>{t.company_name}</a>
                  <div className="pa-muted mono">{t.company_code ?? "—"} · {t.license_code}</div>
                </td>
                <td><span className={`pa-chip pa-chip-${t.status}`}>{ADMIN.status[t.status] ?? t.status}</span></td>
                <td>{t.owner_name ?? <span className="pa-muted">{t.owner_chann_uid ?? "—"}</span>}</td>
                <td className="num">{t.members}</td>
                <td className="num">{t.customers}</td>
                <td className="num">{t.tickets} ({t.open_tickets})</td>
                <td className="num">{t.deals}</td>
                <td>{fmtDate(t.last_activity_at)}</td>
                <td>{fmtDate(t.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
