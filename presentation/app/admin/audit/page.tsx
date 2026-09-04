import { ADMIN } from "@/lib/admin-copy";

import { adminCall, fmtDate, type AuditRow } from "../_server";

const ACTORS = ["", "platform_admin", "system", "user", "ai"] as const;
const copy = ADMIN.audit;

/** Phase 18.1 — the audit trail the platform itself wrote, across tenants,
 *  with the three filters the spec names (cross-tenant, tenant, actor). */
export default async function AdminAudit({
  searchParams,
}: {
  searchParams: Promise<{ scope?: string; license_id?: string; actor_type?: string; action?: string }>;
}) {
  const { scope = "cross", license_id = "", actor_type = "", action = "" } = await searchParams;
  const params = new URLSearchParams({ limit: "200" });
  if (scope === "cross") params.set("cross_tenant", "true");
  if (license_id.trim()) params.set("license_id", license_id.trim());
  if (actor_type) params.set("actor_type", actor_type);
  if (action.trim()) params.set("action", action.trim());
  const rows = await adminCall<AuditRow[]>(`/api/v1/platform/audit?${params.toString()}`);

  return (
    <>
      <div className="pa-head">
        <div>
          <h1>{copy.title}</h1>
          <p>{copy.intro}</p>
        </div>
      </div>

      <form className="pa-filters" method="get" action="/admin/audit">
        <label className="pa-field">
          {copy.scope}
          <select name="scope" defaultValue={scope}>
            <option value="cross">{copy.scopeCross}</option>
            <option value="all">{copy.scopeAll}</option>
          </select>
        </label>
        <label className="pa-field">
          {copy.tenant}
          <input name="license_id" defaultValue={license_id} placeholder={copy.tenantPlaceholder} />
        </label>
        <label className="pa-field">
          {copy.actor}
          <select name="actor_type" defaultValue={actor_type}>
            {ACTORS.map((a) => <option key={a} value={a}>{a || copy.anyActor}</option>)}
          </select>
        </label>
        <label className="pa-field">
          {copy.action}
          <input name="action" defaultValue={action} placeholder={copy.actionPlaceholder} />
        </label>
        <button type="submit" className="pa-btn pa-btn-primary">{copy.filter}</button>
        <a className="pa-btn" href="/admin/audit">{copy.clear}</a>
      </form>

      <div className="pa-table-wrap">
        <table className="pa-table">
          <thead>
            <tr><th>{copy.columns.when}</th><th>{copy.columns.tenant}</th><th>{copy.columns.actor}</th><th>{copy.columns.action}</th><th>{copy.columns.changes}</th><th>{copy.columns.cross}</th></tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={6} className="pa-empty">{copy.empty}</td></tr>}
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{fmtDate(row.created_at)}</td>
                <td className="mono">{row.license_id ? <a href={`/admin/tenants/${row.license_id}`}>{row.license_id.slice(0, 8)}…</a> : "—"}</td>
                <td>{row.actor_type}<div className="pa-muted mono">{row.actor_id ?? ""}</div></td>
                <td>{row.action} <span className="pa-muted">{row.entity_type}</span></td>
                <td className="mono">{row.field_changes ? JSON.stringify(row.field_changes).slice(0, 200) : "—"}</td>
                <td>{row.cross_tenant ? <span className="pa-chip pa-chip-yes">{copy.yes}</span> : <span className="pa-muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
