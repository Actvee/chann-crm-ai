"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

import { adminCall } from "../../_client";
import { fmtDate, type TenantMember, type TenantSummary } from "../../_types";
import { ConfirmDialogBase, useConfirm } from "../../../liff/_confirm";

type Note = { text: string; tone: "ok" | "error" } | null;
const copy = ADMIN.tenant;
const words = copy.members;

/** Round 18 — the members table with per-row actions: change the role,
 *  remove / reactivate, move to another company. The owner's row only
 *  moves through break-glass, so its controls are locked. */
export function TenantMembers({
  licenseId,
  ownerChannUid,
  members,
  tenants,
}: {
  licenseId: string;
  ownerChannUid: string | null;
  members: TenantMember[];
  tenants: TenantSummary[];
}) {
  const router = useRouter();
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState<Note>(null);
  const [roles, setRoles] = useState<Record<string, string>>(
    Object.fromEntries(members.map((m) => [rowKey(m), m.role])),
  );
  const [moving, setMoving] = useState<string>("");
  const [moveTarget, setMoveTarget] = useState("");
  const [moveRole, setMoveRole] = useState("");
  const targets = tenants.filter((t) => t.id !== licenseId && t.status !== "deleted");

  function rowKey(m: TenantMember): string {
    return `${m.chann_uid}:${m.channel ?? "sales"}`;
  }

  function nameOf(m: TenantMember): string {
    return m.display_name ?? m.chann_uid;
  }

  async function call(key: string, path: string, body: unknown, onOk: (body: unknown) => string): Promise<void> {
    setBusy(key);
    setNote(null);
    try {
      const res = await adminCall(path, body);
      if (!res.ok) {
        if (res.status === 401) return;
        setNote({ text: res.reason ? `${words.failed} · ${copy.actions.reason(res.reason)}` : words.failed, tone: "error" });
        return;
      }
      setNote({ text: onOk(res.body), tone: "ok" });
      router.refresh();
    } finally {
      setBusy("");
    }
  }

  async function saveRole(m: TenantMember) {
    const role = (roles[rowKey(m)] ?? "").trim();
    if (!role || role === m.role) return;
    const ok = await ask({
      action: words.roleAction(role),
      target: nameOf(m),
      affects: words.roleAffects,
      reversible: words.roleKeeps,
      confirmLabel: words.roleAction(role),
    });
    if (!ok) return;
    await call(`role:${rowKey(m)}`, `/api/admin/tenants/${licenseId}/members/${encodeURIComponent(m.chann_uid)}/role`, { role }, () => words.roleSaved);
  }

  async function setStatus(m: TenantMember, status: "active" | "removed") {
    const ok = await ask({
      action: status === "removed" ? words.removeAction : words.reactivateAction,
      target: nameOf(m),
      affects: status === "removed" ? words.removeAffects : undefined,
      reversible: words.removeKeeps,
      confirmLabel: status === "removed" ? words.removeAction : words.reactivateAction,
    });
    if (!ok) return;
    await call(`status:${rowKey(m)}`, `/api/admin/tenants/${licenseId}/members/${encodeURIComponent(m.chann_uid)}/status`, { status }, (body) => {
      const n = ((body as { unassigned_tickets?: unknown[] } | null)?.unassigned_tickets ?? []).length;
      if (status === "removed") return n ? `${words.removed} · ${words.unassigned(n)}` : words.removed;
      return words.reactivated;
    });
  }

  async function move(m: TenantMember) {
    const target = targets.find((t) => t.id === moveTarget);
    const role = moveRole.trim();
    if (!target || !role) return;
    const ok = await ask({
      action: words.moveAction(target.company_name),
      target: nameOf(m),
      affects: words.moveAffects,
      permanent: true,
      confirmLabel: words.moveAction(target.company_name),
    });
    if (!ok) return;
    await call(`move:${rowKey(m)}`, `/api/admin/tenants/${licenseId}/members/${encodeURIComponent(m.chann_uid)}/move`,
      { target_license_id: target.id, role }, () => words.moved(target.company_name));
    setMoving("");
    setMoveTarget("");
    setMoveRole("");
  }

  return (
    <section className="pa-card">
      <h2>{copy.membersTitle} ({members.length})</h2>
      <div className="pa-table-wrap">
        <table className="pa-table">
          <thead>
            <tr>
              <th>{copy.memberColumns.name}</th><th>{copy.memberColumns.uid}</th><th>{copy.memberColumns.channel}</th>
              <th>{copy.memberColumns.role}</th><th>{copy.memberColumns.status}</th><th>{copy.memberColumns.joined}</th>
              <th>{copy.memberColumns.actions}</th>
            </tr>
          </thead>
          <tbody>
            {members.length === 0 && <tr><td colSpan={7} className="pa-empty">{copy.membersEmpty}</td></tr>}
            {members.map((m) => {
              const key = rowKey(m);
              const isOwner = m.chann_uid === ownerChannUid && m.role === "owner";
              const rowBusy = busy.endsWith(`:${key}`);
              const active = m.status === "active";
              return (
                <tr key={key}>
                  <td>{m.display_name ?? <span className="pa-muted">—</span>}</td>
                  <td className="mono">{m.chann_uid}</td>
                  <td>{words.channel[m.channel ?? "sales"] ?? m.channel}</td>
                  <td>
                    {isOwner ? (
                      <>{m.role} {copy.ownerTag}</>
                    ) : (
                      <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                        <input aria-label={copy.memberColumns.role}
                          value={roles[key] ?? m.role}
                          onChange={(e) => setRoles((r) => ({ ...r, [key]: e.target.value }))}
                          disabled={rowBusy || !active}
                          style={{ width: 120 }}
                        />
                        <button type="button" className="pa-btn" disabled={rowBusy || !active || (roles[key] ?? m.role) === m.role} onClick={() => void saveRole(m)}>
                          {words.saveRole}
                        </button>
                      </span>
                    )}
                  </td>
                  <td><span className={`pa-chip pa-chip-${active ? "active" : "rejected"}`}>{m.status}</span></td>
                  <td>{fmtDate(m.joined_at)}</td>
                  <td>
                    {isOwner ? (
                      <span className="pa-muted" style={{ fontSize: 12 }}>{words.ownerLocked}</span>
                    ) : (
                      <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
                        {active ? (
                          <button type="button" className="pa-btn pa-btn-danger" disabled={rowBusy} onClick={() => void setStatus(m, "removed")}>
                            {rowBusy ? words.working : words.remove}
                          </button>
                        ) : (
                          <button type="button" className="pa-btn" disabled={rowBusy} onClick={() => void setStatus(m, "active")}>
                            {rowBusy ? words.working : words.reactivate}
                          </button>
                        )}
                        {active && (
                          <button type="button" className="pa-btn" disabled={rowBusy || targets.length === 0} onClick={() => { setMoving(moving === key ? "" : key); setMoveRole(m.role === "technician" ? "technician" : "sales"); }}>
                            {words.move}
                          </button>
                        )}
                      </span>
                    )}
                    {moving === key && (
                      <div className="pa-filters" style={{ marginTop: 8, marginBottom: 0, flexWrap: "wrap", gap: 8 }}>
                        <strong style={{ flexBasis: "100%" }}>{words.moveTitle}</strong>
                        <label className="pa-field">{words.moveTarget}
                          <select value={moveTarget} onChange={(e) => setMoveTarget(e.target.value)} disabled={rowBusy}>
                            <option value="">{words.pickTenant}</option>
                            {targets.map((t) => (
                              <option key={t.id} value={t.id}>{t.company_name} · {t.company_code ?? t.license_code}</option>
                            ))}
                          </select>
                        </label>
                        <label className="pa-field">{words.moveRole}
                          <input value={moveRole} onChange={(e) => setMoveRole(e.target.value)} disabled={rowBusy} style={{ width: 120 }} />
                        </label>
                        <button type="button" className="pa-btn pa-btn-primary" disabled={rowBusy || !moveTarget || !moveRole.trim()} onClick={() => void move(m)}>
                          {rowBusy ? words.working : words.confirmMove}
                        </button>
                        <button type="button" className="pa-btn" disabled={rowBusy} onClick={() => setMoving("")}>{words.cancel}</button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {note && <p className={`pa-note pa-note-${note.tone}`} role="status">{note.text}</p>}
      <ConfirmDialogBase
        request={confirming}
        onClose={closeConfirm}
        busy={Boolean(busy)}
        copy={{ cancel: ADMIN.confirm.keepIt, permanent: ADMIN.confirm.cannotUndo }}
      />
    </section>
  );
}
