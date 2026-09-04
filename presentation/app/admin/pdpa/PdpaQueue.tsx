"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

import { fmtDate, type PdpaRequest } from "../_types";

const copy = ADMIN.pdpa;

export function PdpaQueue({ rows }: { rows: PdpaRequest[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState<{ text: string; tone: "ok" | "error" } | null>(null);
  const [newUid, setNewUid] = useState("");
  const [newType, setNewType] = useState("export");

  async function act(id: string, action: "process" | "reject") {
    const request = rows.find((r) => r.id === id);
    let reason = "";
    if (action === "reject") {
      reason = window.prompt(copy.rejectReason) ?? "";
      if (!reason.trim()) return;
    } else if (request?.request_type === "erasure") {
      if (!window.confirm(copy.confirmErase(request.chann_uid))) return;
    }
    setBusy(id);
    setNote(null);
    try {
      const res = await fetch(`/api/admin/pdpa/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setNote({ text: action === "process" ? copy.processed : copy.rejected, tone: "ok" });
      router.refresh();
    } catch {
      setNote({ text: copy.failed, tone: "error" });
    } finally {
      setBusy("");
    }
  }

  async function create() {
    if (!newUid.trim()) return;
    setBusy("new");
    setNote(null);
    try {
      const res = await fetch("/api/admin/pdpa/new/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chann_uid: newUid.trim(), request_type: newType }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setNote({ text: copy.created, tone: "ok" });
      setNewUid("");
      router.refresh();
    } catch {
      setNote({ text: copy.createFailed, tone: "error" });
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <section className="pa-card" style={{ marginBottom: 16 }}>
        <h2>{copy.createTitle}</h2>
        <div className="pa-filters" style={{ marginBottom: 0 }}>
          <label className="pa-field">
            {copy.uid}
            <input value={newUid} onChange={(e) => setNewUid(e.target.value)} placeholder="CHN-…" />
          </label>
          <label className="pa-field">
            {copy.type}
            <select value={newType} onChange={(e) => setNewType(e.target.value)}>
              <option value="export">{copy.types.export}</option>
              <option value="erasure">{copy.types.erasure}</option>
              <option value="consent_withdraw">{copy.types.consent_withdraw}</option>
            </select>
          </label>
          <button type="button" className="pa-btn pa-btn-primary" disabled={busy !== "" || !newUid.trim()} onClick={() => void create()}>
            {busy === "new" ? copy.creating : copy.create}
          </button>
        </div>
        {note && <p className={`pa-note pa-note-${note.tone}`} role="status">{note.text}</p>}
      </section>

      <div className="pa-table-wrap">
        <table className="pa-table">
          <thead>
            <tr><th>{copy.columns.when}</th><th>{copy.columns.who}</th><th>{copy.columns.type}</th><th>{copy.columns.via}</th><th>{copy.columns.status}</th><th>{copy.columns.result}</th><th></th></tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={7} className="pa-empty">{copy.empty}</td></tr>}
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{fmtDate(r.requested_at)}</td>
                <td className="mono">{r.chann_uid}</td>
                <td>{copy.types[r.request_type] ?? r.request_type}</td>
                <td>{r.requested_via}</td>
                <td><span className={`pa-chip pa-chip-${r.status}`}>{ADMIN.status[r.status] ?? r.status}</span></td>
                <td className="mono">
                  {r.status === "rejected" ? r.rejection_reason : r.result_json ? JSON.stringify(r.result_json).slice(0, 120) : "—"}
                </td>
                <td>
                  {r.status === "pending" && r.request_type !== "consent_withdraw" && (
                    <div className="pa-actions">
                      <button type="button" className="pa-btn pa-btn-sm pa-btn-primary" disabled={busy !== ""} onClick={() => void act(r.id, "process")}>
                        {busy === r.id ? "…" : copy.process}
                      </button>
                      <button type="button" className="pa-btn pa-btn-sm" disabled={busy !== ""} onClick={() => void act(r.id, "reject")}>
                        {copy.reject}
                      </button>
                    </div>
                  )}
                  {r.status === "pending" && r.request_type === "consent_withdraw" && (
                    <button type="button" className="pa-btn pa-btn-sm" disabled={busy !== ""} onClick={() => void act(r.id, "reject")}>
                      {copy.close}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
