"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

import { adminCall } from "../../_client";
import { bangkokDay, fmtDate, type TenantDetail, type TenantEditFields } from "../../_types";

type Note = { text: string; tone: "ok" | "error" } | null;
const copy = ADMIN.tenant;
const edit = copy.edit;

function fieldsOf(tenant: TenantDetail): TenantEditFields {
  return {
    company_name: tenant.company_name ?? "",
    legal_name: tenant.legal_name ?? "",
    company_phone: tenant.company_phone ?? "",
    company_email: tenant.company_email ?? "",
    company_address: tenant.company_address ?? "",
    tax_id: tenant.tax_id ?? "",
    trial_expires_at: bangkokDay(tenant.trial_expires_at),
    status: tenant.status,
  };
}

/** The shop's details, readable at a glance and editable in place: name,
 *  legal name, phone, email, address, tax id, the trial deadline and the
 *  trial/active status. Suspending stays with the actions card. Until
 *  7 Sep 2026 none of this could be changed from the console. */
export function TenantEdit({ tenant }: { tenant: TenantDetail }) {
  const router = useRouter();
  const initial = fieldsOf(tenant);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<TenantEditFields>(initial);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);

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
    if (diff.company_name !== undefined && !diff.company_name.trim()) {
      setNote({ text: edit.companyNameRequired, tone: "error" });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const res = await adminCall(`/api/admin/tenants/${tenant.id}`, diff);
      if (!res.ok) {
        if (res.status === 401) return;
        setNote({ text: res.reason ? `${edit.failed} · ${copy.actions.reason(res.reason)}` : edit.failed, tone: "error" });
        return;
      }
      setNote({ text: edit.saved, tone: "ok" });
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
          <dt>{copy.trialUntil}</dt><dd>{fmtDate(tenant.trial_expires_at)}</dd>
          <dt>{copy.created}</dt><dd>{fmtDate(tenant.created_at)}</dd>
          <dt>{copy.lastActivity}</dt><dd>{fmtDate(tenant.last_activity_at)}</dd>
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
          <input type="date" value={form.trial_expires_at} onChange={(e) => set("trial_expires_at", e.target.value)} disabled={busy} />
          <span className="pa-muted" style={{ fontSize: 12 }}>{edit.trialHint}</span>
        </label>
        <label className="pa-field">{edit.status}
          <select value={form.status} onChange={(e) => set("status", e.target.value)} disabled={busy || tenant.status === "suspended"}>
            <option value="trial">{ADMIN.status.trial}</option>
            <option value="active">{ADMIN.status.active}</option>
            {tenant.status === "suspended" && <option value="suspended">{ADMIN.status.suspended}</option>}
          </select>
          <span className="pa-muted" style={{ fontSize: 12 }}>{edit.statusHint}</span>
        </label>
      </div>
      <div className="pa-actions" style={{ marginTop: 12 }}>
        <button type="button" className="pa-btn pa-btn-primary" disabled={busy} onClick={() => void save()}>
          {busy ? edit.saving : edit.save}
        </button>
        <button type="button" className="pa-btn" disabled={busy} onClick={() => { setEditing(false); setNote(null); }}>
          {edit.cancel}
        </button>
      </div>
      {note && <p className={`pa-note pa-note-${note.tone}`} role="status">{note.text}</p>}
    </section>
  );
}
