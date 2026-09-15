import { notFound } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";
import { ApplicationError } from "@/lib/api";

import { adminCall, fmtDate, type AuditRow, type TenantDetail, type TenantSummary } from "../../_server";
import { TenantActions } from "./TenantActions";
import { TenantDelete } from "./TenantDelete";
import { TenantEdit } from "./TenantEdit";
import { TenantMembers } from "./TenantMembers";

const copy = ADMIN.tenant;

/** Phase 18 — one tenant: who is in it, how big it is, and what the
 *  operator may do: suspend/reopen, renew, break-glass owner transfer,
 *  manage members, delete the company (round 18). */
export default async function AdminTenant({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let tenant: TenantDetail;
  try {
    tenant = await adminCall<TenantDetail>(`/api/v1/platform/tenants/${id}`);
  } catch (error) {
    if (error instanceof ApplicationError && error.status === 404) notFound();
    throw error;
  }
  const audit = await adminCall<AuditRow[]>(`/api/v1/platform/audit?license_id=${id}&limit=20`);
  // Every other live company, for "move this member to…".
  const tenants = await adminCall<TenantSummary[]>("/api/v1/platform/tenants");
  const activeMembers = tenant.members_detail.filter((m) => m.status === "active");

  return (
    <>
      <div className="pa-crumb"><a href="/admin">{copy.crumb}</a> / {tenant.company_name}</div>
      <div className="pa-head">
        <div>
          <h1>{tenant.company_name}</h1>
          <p>
            <span className={`pa-chip pa-chip-${tenant.status}`}>{ADMIN.status[tenant.status] ?? tenant.status}</span>
            {" "}· {copy.shopCode} <span className="mono">{tenant.company_code ?? "—"}</span> · license <span className="mono">{tenant.license_code}</span>
          </p>
        </div>
      </div>

      <div className="pa-metrics">
        <div className="pa-metric"><div className="pa-metric-label">{copy.metrics.members}</div><div className="pa-metric-value">{tenant.members}</div></div>
        <div className="pa-metric"><div className="pa-metric-label">{copy.metrics.customers}</div><div className="pa-metric-value">{tenant.customers}</div></div>
        <div className="pa-metric"><div className="pa-metric-label">{copy.metrics.tickets}</div><div className="pa-metric-value">{tenant.tickets}<small>{copy.metrics.open} {tenant.open_tickets}</small></div></div>
        <div className="pa-metric"><div className="pa-metric-label">{copy.metrics.deals}</div><div className="pa-metric-value">{tenant.deals}</div></div>
      </div>

      <div className="pa-grid-2">
        <TenantEdit tenant={tenant} />
        <TenantActions
          licenseId={tenant.id}
          status={tenant.status}
          expiresAt={tenant.expires_at}
          ownerChannUid={tenant.owner_chann_uid}
          members={activeMembers}
        />
      </div>

      <TenantMembers
        licenseId={tenant.id}
        ownerChannUid={tenant.owner_chann_uid}
        members={tenant.members_detail}
        tenants={tenants}
      />

      <section className="pa-card">
        <h2>{copy.auditTitle}</h2>
        <div className="pa-table-wrap">
          <table className="pa-table">
            <thead><tr><th>{copy.auditColumns.when}</th><th>{copy.auditColumns.actor}</th><th>{copy.auditColumns.action}</th><th>{copy.auditColumns.changes}</th><th>{copy.auditColumns.cross}</th></tr></thead>
            <tbody>
              {audit.length === 0 && <tr><td colSpan={5} className="pa-empty">{copy.auditEmpty}</td></tr>}
              {audit.map((row) => (
                <tr key={row.id}>
                  <td>{fmtDate(row.created_at)}</td>
                  <td>{row.actor_type}<div className="pa-muted mono">{row.actor_id ?? ""}</div></td>
                  <td>{row.action} <span className="pa-muted">{row.entity_type}</span></td>
                  <td className="mono">{row.field_changes ? JSON.stringify(row.field_changes).slice(0, 160) : "—"}</td>
                  <td>{row.cross_tenant ? <span className="pa-chip pa-chip-yes">{copy.yes}</span> : <span className="pa-muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <TenantDelete
        licenseId={tenant.id}
        companyName={tenant.company_name}
        companyCode={tenant.company_code}
        status={tenant.status}
      />
    </>
  );
}
