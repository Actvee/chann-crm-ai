"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

import type { TenantMember } from "../../_types";

type Note = { text: string; tone: "ok" | "error" } | null;
const copy = ADMIN.tenant.actions;

/** The two things an operator may do to a tenant (18.1, 18.4). Both ask
 *  for a confirmation; both are audited by the Data tier. */
export function TenantActions({
  licenseId,
  status,
  ownerChannUid,
  members,
}: {
  licenseId: string;
  status: string;
  ownerChannUid: string | null;
  members: TenantMember[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"" | "status" | "transfer">("");
  const [note, setNote] = useState<Note>(null);
  const [target, setTarget] = useState("");
  const suspended = status === "suspended";
  const candidates = members.filter((m) => m.chann_uid !== ownerChannUid);

  async function setStatus(next: "active" | "suspended") {
    if (!window.confirm(next === "suspended" ? copy.confirmSuspend : copy.confirmReopen)) return;
    setBusy("status");
    setNote(null);
    try {
      const res = await fetch(`/api/admin/tenants/${licenseId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: next }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setNote({ text: next === "suspended" ? copy.suspended : copy.reopened, tone: "ok" });
      router.refresh();
    } catch {
      setNote({ text: copy.failed, tone: "error" });
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
      const res = await fetch("/api/admin/break-glass", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ license_id: licenseId, target_chann_uid: target }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setNote({ text: copy.transferred(name), tone: "ok" });
      setTarget("");
      router.refresh();
    } catch {
      setNote({ text: copy.transferFailed, tone: "error" });
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="pa-card">
      <h2>{copy.title}</h2>
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
      <p className="pa-muted" style={{ margin: "8px 0 18px", fontSize: 13 }}>{copy.note}</p>

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
