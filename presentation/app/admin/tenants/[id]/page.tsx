import { notFound } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";
import { ApplicationError } from "@/lib/api";

import { adminCall, fmtDate, type AuditRow, type TenantDetail } from "../../_server";
import { TenantActions } from "./TenantActions";

const copy = ADMIN.tenant;

/** Phase 18 — one tenant: who is in it, how big it is, and the two
 *  operator actions (suspend/reopen, break-glass owner transfer). */
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
        <section className="pa-card">
          <h2>{copy.info}</h2>
          <dl className="pa-kv">
            <dt>{copy.owner}</dt><dd>{tenant.owner_name ?? "—"} <span className="pa-muted mono">{tenant.owner_chann_uid ?? ""}</span></dd>
            <dt>{copy.legalName}</dt><dd>{tenant.legal_name ?? "—"}</dd>
            <dt>{copy.phone}</dt><dd>{tenant.company_phone ?? "—"}</dd>
            <dt>{copy.email}</dt><dd>{tenant.company_email ?? "—"}</dd>
            <dt>{copy.trialUntil}</dt><dd>{fmtDate(tenant.trial_expires_at)}</dd>
            <dt>{copy.created}</dt><dd>{fmtDate(tenant.created_at)}</dd>
            <dt>{copy.lastActivity}</dt><dd>{fmtDate(tenant.last_activity_at)}</dd>
          </dl>
        </section>
        <TenantActions
          licenseId={tenant.id}
          status={tenant.status}
          ownerChannUid={tenant.owner_chann_uid}
          members={activeMembers}
        />
      </div>

      <section className="pa-card">
        <h2>{copy.membersTitle} ({tenant.members_detail.length})</h2>
        <div className="pa-table-wrap">
          <table className="pa-table">
            <thead><tr><th>{copy.memberColumns.name}</th><th>{copy.memberColumns.uid}</th><th>{copy.memberColumns.role}</th><th>{copy.memberColumns.status}</th><th>{copy.memberColumns.joined}</th></tr></thead>
            <tbody>
              {tenant.members_detail.length === 0 && <tr><td colSpan={5} className="pa-empty">{copy.membersEmpty}</td></tr>}
              {tenant.members_detail.map((m) => (
                <tr key={m.chann_uid}>
                  <td>{m.display_name ?? <span className="pa-muted">—</span>}</td>
                  <td className="mono">{m.chann_uid}</td>
                  <td>{m.role}{m.chann_uid === tenant.owner_chann_uid ? ` ${copy.ownerTag}` : ""}</td>
                  <td><span className={`pa-chip pa-chip-${m.status === "active" ? "active" : "rejected"}`}>{m.status}</span></td>
                  <td>{fmtDate(m.joined_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

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
    </>
  );
}
