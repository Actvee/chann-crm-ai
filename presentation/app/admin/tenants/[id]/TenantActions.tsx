"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

import { adminCall } from "../../_client";
import { fmtDate, isExpired, type TenantMember } from "../../_types";

type Note = { text: string; tone: "ok" | "error" } | null;
const copy = ADMIN.tenant.actions;

/** What an operator may do to a tenant: suspend/reopen (18.1), renew the
 *  subscription (round 18), break-glass owner transfer (18.4). Every
 *  action asks for a confirmation; every one is audited by the Data tier. */
export function TenantActions({
  licenseId,
  status,
  expiresAt,
  ownerChannUid,
  members,
}: {
  licenseId: string;
  status: string;
  expiresAt: string | null;
  ownerChannUid: string | null;
  members: TenantMember[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"" | "status" | "extend" | "transfer">("");
  const [note, setNote] = useState<Note>(null);
  const [target, setTarget] = useState("");
  const suspended = status === "suspended";
  const deleted = status === "deleted";
  const expired = isExpired(expiresAt);
  const candidates = members.filter((m) => m.chann_uid !== ownerChannUid);

  async function extend(days: number, { reopen = false } = {}) {
    if (!window.confirm(reopen ? copy.confirmReopenExpired : copy.confirmExtend(days))) return;
    setBusy(reopen ? "status" : "extend");
    setNote(null);
    try {
      const res = await adminCall(`/api/admin/tenants/${licenseId}/extend`, { days });
      if (!res.ok) {
        if (res.status === 401) return;
        setNote({ text: res.reason ? `${copy.failed} · ${copy.reason(res.reason)}` : copy.failed, tone: "error" });
        return;
      }
      const until = (res.body as { expires_at?: string | null } | null)?.expires_at ?? null;
      setNote({ text: reopen ? copy.reopenedExtended : copy.extended(days, fmtDate(until)), tone: "ok" });
      router.refresh();
    } finally {
      setBusy("");
    }
  }

  async function setStatus(next: "active" | "suspended") {
    // Reopening a shop whose subscription already ended would suspend it
    // again on the next sweep — so the reopen renews 30 days and says so.
    if (next === "active" && expired) {
      await extend(30, { reopen: true });
      return;
    }
    if (!window.confirm(next === "suspended" ? copy.confirmSuspend : copy.confirmReopen)) return;
    setBusy("status");
    setNote(null);
    try {
      const res = await adminCall(`/api/admin/tenants/${licenseId}/status`, { status: next });
      if (!res.ok) {
        if (res.status === 401) return;
        setNote({ text: res.reason ? `${copy.failed} · ${copy.reason(res.reason)}` : copy.failed, tone: "error" });
        return;
      }
      setNote({ text: next === "suspended" ? copy.suspended : copy.reopened, tone: "ok" });
      router.refresh();
    } finally {
      setBusy("");
    }
  }

  async function transfer() {
    if (!target) return;
    const member = candidates.find((m) => m.chann_uid === target);
    const name = member?.display_name ?? target;
    if (!window.confirm(copy.confirmTransfer(name))) return;
    setBusy("transfer");
    setNote(null);
    try {
      const res = await adminCall("/api/admin/break-glass", { license_id: licenseId, target_chann_uid: target });
      if (!res.ok) {
        if (res.status === 401) return;
        // The Application tier's reason (a 404 "member not found", a 409
        // "not active") used to arrive as a bare 502 (review D11).
        setNote({
          text: res.reason ? `${copy.transferFailed} · ${copy.reason(res.reason)}` : copy.transferFailed,
          tone: "error",
        });
        return;
      }
      setNote({ text: copy.transferred(name), tone: "ok" });
      setTarget("");
      router.refresh();
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="pa-card">
      <h2>{copy.title}</h2>
      {deleted ? (
        <p className="pa-note pa-note-error" role="status">{copy.deletedNote}</p>
      ) : (
        <div className="pa-actions">
          {suspended ? (
            <button type="button" className="pa-btn pa-btn-primary" disabled={busy !== ""} onClick={() => void setStatus("active")}>
              {busy === "status" ? copy.working : copy.reopen}
            </button>
          ) : (
            <button type="button" className="pa-btn pa-btn-danger" disabled={busy !== ""} onClick={() => void setStatus("suspended")}>
              {busy === "status" ? copy.working : copy.suspend}
            </button>
          )}
        </div>
      )}
      <p className="pa-muted" style={{ margin: "8px 0 18px", fontSize: 13 }}>{copy.note}</p>

      <h2>{copy.subscriptionTitle}</h2>
      <p style={{ margin: "0 0 8px" }}>
        {copy.expiresOn}: <strong>{expiresAt ? fmtDate(expiresAt) : copy.noExpiry}</strong>
        {expired && <span className="pa-chip pa-chip-suspended" style={{ marginLeft: 8 }}>{copy.expired}</span>}
      </p>
      <div className="pa-actions">
        <button type="button" className="pa-btn pa-btn-primary" disabled={busy !== "" || deleted} onClick={() => void extend(30)}>
          {busy === "extend" ? copy.working : copy.extend30}
        </button>
        <button type="button" className="pa-btn" disabled={busy !== "" || deleted} onClick={() => void extend(365)}>
          {busy === "extend" ? copy.working : copy.extend365}
        </button>
      </div>
      <p className="pa-muted" style={{ margin: "8px 0 18px", fontSize: 13 }}>{copy.extendNote}</p>

      <h2>{copy.breakGlassTitle}</h2>
      <div className="pa-filters" style={{ marginBottom: 0 }}>
        <label className="pa-field">
          {copy.newOwner}
          <select value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy !== "" || candidates.length === 0}>
            <option value="">{candidates.length === 0 ? copy.noCandidates : copy.pickMember}</option>
            {candidates.map((m) => (
              <option key={m.chann_uid} value={m.chann_uid}>
                {(m.display_name ?? m.chann_uid) + " · " + m.role}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="pa-btn pa-btn-danger" disabled={busy !== "" || !target} onClick={() => void transfer()}>
          {busy === "transfer" ? copy.transferring : copy.transfer}
        </button>
      </div>
      {note && (
        <p className={`pa-note pa-note-${note.tone}`} role="status">{note.text}</p>
      )}
    </section>
  );
}
