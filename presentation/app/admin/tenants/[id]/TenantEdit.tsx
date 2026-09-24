"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

import { adminCall } from "../../_client";
import { bangkokDay, fmtDate, type PlanPreview, type TenantDetail, type TenantEditFields } from "../../_types";
import { ConfirmDialogBase, useConfirm } from "../../../liff/_confirm";

type Note = { text: string; tone: "ok" | "error" } | null;
const copy = ADMIN.tenant;
const edit = copy.edit;
/** Round 21D — the plans, lowest first (entitlements.PLAN_ORDER). */
const PLAN_CODES = ["starter", "pro", "enterprise", "enterprise_plus"] as const;

/** "จะล็อก: งานบริการ / งานซ่อม และทีมช่าง (ใบงานเปิดอยู่ 3 · ช่าง 2)" — one
 *  line per feature the move would lock, with what it holds today. */
function lockLines(preview: PlanPreview): string[] {
  return preview.locks.map((lock) => {
    const counts = Object.entries(lock.counts).filter(([, n]) => n > 0)
      .map(([word, n]) => `${edit.countWords[word] ?? word} ${n}`).join(" · ");
    return `${edit.planLocks} ${edit.featureNames[lock.feature] ?? lock.feature}${counts ? ` (${counts})` : ""}`;
  });
}

function fieldsOf(tenant: TenantDetail): TenantEditFields {
  return {
    company_name: tenant.company_name ?? "",
    legal_name: tenant.legal_name ?? "",
    company_phone: tenant.company_phone ?? "",
    company_email: tenant.company_email ?? "",
    company_address: tenant.company_address ?? "",
    tax_id: tenant.tax_id ?? "",
    admin_notes: tenant.admin_notes ?? "",
    expires_at: bangkokDay(tenant.expires_at),
    status: tenant.status,
    ai_chart_quota: tenant.ai_chart_quota == null ? "" : String(tenant.ai_chart_quota),
    plan_code: tenant.plan_code ?? "pro",
  };
}

/** The shop's details, readable at a glance and editable in place: name,
 *  legal name, phone, email, address, tax id, the subscription's end
 *  date (any status — round 18), the operator's own notes and the
 *  trial/active status. Suspending stays with the actions card; a
 *  soft-deleted company is restored here by picking trial/active. */
export function TenantEdit({ tenant }: { tenant: TenantDetail }) {
  const router = useRouter();
  const initial = fieldsOf(tenant);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<TenantEditFields>(initial);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  // The Data tier's own refusal sentence (409 plan_member_limit) when a
  // save got past the preview — shown by the plan field, not as a toast.
  const [planError, setPlanError] = useState<string | null>(null);
  // The API's refusal of a top-up (Ruling 28: not on Starter), by its field.
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const { request: confirming, ask, close: closeConfirm } = useConfirm();

  // Round 21D — what the picked plan would lock, and whether it is refused
  // (owner decision Q2: more active members than its limit — no override).
  const previews = tenant.plan_preview?.previews ?? {};
  const planChanged = form.plan_code !== initial.plan_code;
  const preview = planChanged ? previews[form.plan_code] : undefined;
  const refused = preview?.refused ? preview : undefined;
  const planAlert = refused ? edit.planRefused(refused.inactivate, refused.label) : planError;

  function set<K extends keyof TenantEditFields>(key: K, value: TenantEditFields[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function changed(): Partial<TenantEditFields> {
    const out: Partial<TenantEditFields> = {};
    (Object.keys(form) as (keyof TenantEditFields)[]).forEach((k) => {
      if (form[k] !== initial[k]) out[k] = form[k];
    });
    return out;
  }

  async function save() {
    const diff = changed();
    if (Object.keys(diff).length === 0) {
      setNote({ text: edit.noChanges, tone: "error" });
      return;
    }
    // Blank means "leave it at the default", which is not the same as 0.
    if (diff.ai_chart_quota !== undefined && diff.ai_chart_quota.trim()
        && !/^\d+$/.test(diff.ai_chart_quota.trim())) {
      setNote({ text: edit.aiChartQuotaNumber, tone: "error" });
      return;
    }
    if (diff.company_name !== undefined && !diff.company_name.trim()) {
      setNote({ text: edit.companyNameRequired, tone: "error" });
      return;
    }
    // Starter has no AI reports, so there is nothing to top up.
    if (form.plan_code === "starter") delete diff.ai_chart_quota;
    if (diff.plan_code !== undefined) {
      if (refused) return;
      const label = preview?.label ?? edit.planNames[diff.plan_code] ?? diff.plan_code;
      const affects = !tenant.plan_preview ? [edit.planPreviewMissing]
        : preview && preview.locks.length ? lockLines(preview) : [edit.planLocksNone];
      const ok = await ask({
        action: edit.planConfirm(label), target: tenant.company_name,
        affects, reversible: edit.planKeeps, confirmLabel: edit.planConfirm(label),
      });
      if (!ok) return;
    }
    setBusy(true);
    setNote(null);
    setPlanError(null);
    setQuotaError(null);
    try {
      const res = await adminCall(`/api/admin/tenants/${tenant.id}`, diff);
      if (!res.ok) {
        if (res.status === 401) return;
        if (res.status === 409 && diff.plan_code !== undefined && res.reason) {
          // The Data tier refused the plan (a page older than the last
          // member change): its sentence, verbatim, by the plan field.
          setPlanError(res.reason);
          return;
        }
        if (res.status === 422 && diff.ai_chart_quota !== undefined && res.reason) {
          // Ruling 28: nothing was written; the API's sentence, by the field.
          setQuotaError(res.reason);
          return;
        }
        setNote({ text: res.reason ? `${edit.failed} · ${copy.actions.reason(res.reason)}` : edit.failed, tone: "error" });
        return;
      }
      // The tenant (and maybe its plan) saved, the top-up did not: say both.
      const saved = res.body as { quota_error?: string; plan_changed?: boolean } | null;
      if (saved?.quota_error) {
        const head = saved.plan_changed ? edit.planSavedQuotaFailed : edit.savedQuotaFailed;
        setNote({ text: `${head} · ${copy.actions.reason(saved.quota_error)}`, tone: "error" });
      } else {
        setNote({ text: edit.saved, tone: "ok" });
      }
      setEditing(false);
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <section className="pa-card">
        <div className="pa-head" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>{copy.info}</h2>
          <button type="button" className="pa-btn" onClick={() => { setForm(fieldsOf(tenant)); setNote(null); setEditing(true); }}>
            {edit.open}
          </button>
        </div>
        <dl className="pa-kv">
          <dt>{copy.owner}</dt><dd>{tenant.owner_name ?? "—"} <span className="pa-muted mono">{tenant.owner_chann_uid ?? ""}</span></dd>
          <dt>{copy.legalName}</dt><dd>{tenant.legal_name ?? "—"}</dd>
          <dt>{copy.phone}</dt><dd>{tenant.company_phone ?? "—"}</dd>
          <dt>{copy.email}</dt><dd>{tenant.company_email ?? "—"}</dd>
          <dt>{edit.address_shown}</dt><dd>{tenant.company_address ?? "—"}</dd>
          <dt>{edit.taxId_shown}</dt><dd>{tenant.tax_id ?? "—"}</dd>
          <dt>{copy.trialUntil}</dt><dd>{fmtDate(tenant.expires_at)}</dd>
          {tenant.deleted_at && <><dt>{copy.deletedAt}</dt><dd>{fmtDate(tenant.deleted_at)}</dd></>}
          <dt>{copy.created}</dt><dd>{fmtDate(tenant.created_at)}</dd>
          <dt>{copy.lastActivity}</dt><dd>{fmtDate(tenant.last_activity_at)}</dd>
          <dt>{edit.plan}</dt>
          <dd>
            {edit.planNames[tenant.plan_code ?? "pro"] ?? tenant.plan_code}
            {tenant.usage && (
              <span className="pa-muted"> · {edit.usageUsers(tenant.usage.members, tenant.usage.members_limit)}
                {" · "}{edit.usageAi(tenant.usage.ai_reports_used, tenant.usage.ai_reports_allowance)}</span>
            )}
          </dd>
          <dt>{edit.aiChartQuota}</dt>
          <dd>
            {tenant.ai_chart_quota == null ? edit.aiChartDefault : String(tenant.ai_chart_quota)}
            {!tenant.usage && <span className="pa-muted"> · {edit.aiChartUsed} {tenant.ai_chart_used ?? 0}</span>}
          </dd>
          <dt>{copy.adminNotes}</dt><dd style={{ whiteSpace: "pre-wrap" }}>{tenant.admin_notes?.trim() ? tenant.admin_notes : "—"}</dd>
        </dl>
        {note && <p className={`pa-note pa-note-${note.tone}`} role="status">{note.text}</p>}
      </section>
    );
  }

  return (
    <section className="pa-card">
      <h2>{copy.info}</h2>
      <div className="pa-filters" style={{ flexWrap: "wrap", gap: 12 }}>
        <label className="pa-field">{edit.companyName}
          <input value={form.company_name} onChange={(e) => set("company_name", e.target.value)} disabled={busy} required />
        </label>
        <label className="pa-field">{edit.legalName}
          <input value={form.legal_name} onChange={(e) => set("legal_name", e.target.value)} disabled={busy} />
        </label>
        <label className="pa-field">{edit.phone}
          <input value={form.company_phone} onChange={(e) => set("company_phone", e.target.value)} disabled={busy} inputMode="tel" />
        </label>
        <label className="pa-field">{edit.email}
          <input value={form.company_email} onChange={(e) => set("company_email", e.target.value)} disabled={busy} inputMode="email" />
        </label>
        <label className="pa-field">{edit.taxId}
          <input value={form.tax_id} onChange={(e) => set("tax_id", e.target.value)} disabled={busy} maxLength={13} />
        </label>
        <label className="pa-field" style={{ flexBasis: "100%" }}>{edit.address}
          <textarea value={form.company_address} onChange={(e) => set("company_address", e.target.value)} disabled={busy} />
        </label>
        <label className="pa-field">{edit.trialUntil}
          <input type="date" value={form.expires_at} onChange={(e) => set("expires_at", e.target.value)} disabled={busy} />
          <span className="pa-muted" style={{ fontSize: 12 }}>{edit.trialHint}</span>
        </label>
        <div className="pa-field" style={{ flexBasis: "100%" }}>
          <label htmlFor="plan-code">{edit.plan}</label>
          <select
            id="plan-code"
            value={form.plan_code}
            onChange={(e) => { set("plan_code", e.target.value); setPlanError(null); setQuotaError(null); }}
            disabled={busy}
            aria-invalid={planAlert ? true : undefined}
            aria-describedby={planAlert ? "plan-error plan-hint" : "plan-hint"}
          >
            {PLAN_CODES.map((code) => (
              <option key={code} value={code}>
                {previews[code]?.refused
                  ? edit.planOptionBlocked(edit.planNames[code], previews[code].inactivate)
                  : edit.planNames[code]}
              </option>
            ))}
          </select>
          <span id="plan-hint" className="pa-muted" style={{ fontSize: 12 }}>{edit.planHint}</span>
          {planAlert ? (
            <p id="plan-error" className="pa-note pa-note-error" role="alert" style={{ marginTop: 4 }}>
              {planAlert} · <a href="#tenant-members">{edit.planRefusedWhere}</a>
            </p>
          ) : planChanged && (
            <p className="pa-note pa-note-warn" style={{ marginTop: 4 }}>
              {!tenant.plan_preview ? edit.planPreviewMissing
                : preview && preview.locks.length ? lockLines(preview).join(" · ") : edit.planLocksNone}
            </p>
          )}
        </div>
        <label className="pa-field">{edit.aiChartQuota}
          <input
            id="ai-chart-quota"
            type="number"
            min={0}
            value={form.ai_chart_quota}
            onChange={(e) => { set("ai_chart_quota", e.target.value); setQuotaError(null); }}
            disabled={busy || form.plan_code === "starter"}
            placeholder={edit.aiChartDefault}
            aria-invalid={quotaError ? true : undefined}
            aria-describedby={[quotaError && "ai-chart-error", form.plan_code === "starter" && "ai-chart-starter", "ai-chart-hint"]
              .filter(Boolean).join(" ")}
          />
          {quotaError && (
            <span id="ai-chart-error" className="pa-note pa-note-error" role="alert" style={{ marginTop: 4 }}>{quotaError}</span>
          )}
          {form.plan_code === "starter" && (
            <span id="ai-chart-starter" className="pa-note pa-note-warn" style={{ marginTop: 4 }}>{edit.aiChartStarter}</span>
          )}
          <span id="ai-chart-hint" className="pa-muted" style={{ fontSize: 12 }}>{edit.aiChartQuotaHint}</span>
        </label>
        <label className="pa-field">{edit.status}
          <select value={form.status} onChange={(e) => set("status", e.target.value)} disabled={busy || tenant.status === "suspended"}>
            <option value="trial">{ADMIN.status.trial}</option>
            <option value="active">{ADMIN.status.active}</option>
            {tenant.status === "suspended" && <option value="suspended">{ADMIN.status.suspended}</option>}
            {tenant.status === "deleted" && <option value="deleted">{ADMIN.status.deleted}</option>}
          </select>
          <span className="pa-muted" style={{ fontSize: 12 }}>{edit.statusHint}</span>
        </label>
        <label className="pa-field" style={{ flexBasis: "100%" }}>{edit.adminNotes}
          <textarea value={form.admin_notes} onChange={(e) => set("admin_notes", e.target.value)} disabled={busy} rows={3} />
          <span className="pa-muted" style={{ fontSize: 12 }}>{edit.adminNotesHint}</span>
        </label>
      </div>
      <div className="pa-actions" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="pa-btn pa-btn-primary"
          disabled={busy || Boolean(refused)}
          aria-describedby={refused ? "plan-error" : undefined}
          onClick={() => void save()}
        >
          {busy ? edit.saving : edit.save}
        </button>
        <button type="button" className="pa-btn" disabled={busy} onClick={() => { setEditing(false); setNote(null); setPlanError(null); setQuotaError(null); }}>
          {edit.cancel}
        </button>
      </div>
      {note && <p className={`pa-note pa-note-${note.tone}`} role="status">{note.text}</p>}
      <ConfirmDialogBase
        request={confirming}
        onClose={closeConfirm}
        busy={busy}
        copy={{ cancel: ADMIN.confirm.keepIt, permanent: ADMIN.confirm.cannotUndo }}
      />
    </section>
  );
}
