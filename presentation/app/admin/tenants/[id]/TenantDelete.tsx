"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

import { adminCall } from "../../_client";

type Note = { text: string; tone: "ok" | "error" } | null;
const copy = ADMIN.tenant.delete;

/** Round 18 — delete a company. The button only wakes up once the
 *  operator has typed the company code; a checkbox turns the reversible
 *  soft delete into a purge, which the Application tier only allows with
 *  the break-glass permission. */
export function TenantDelete({
  licenseId,
  companyName,
  companyCode,
  status,
}: {
  licenseId: string;
  companyName: string;
  companyCode: string | null;
  status: string;
}) {
  const router = useRouter();
  const [typed, setTyped] = useState("");
  const [purge, setPurge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const code = (companyCode ?? "").trim().toUpperCase();
  const armed = code !== "" && typed.trim().toUpperCase() === code;
  const alreadyDeleted = status === "deleted";

  async function remove() {
    if (!armed) return;
    if (alreadyDeleted && !purge) {
      setNote({ text: copy.alreadyDeleted, tone: "error" });
      return;
    }
    if (!window.confirm(purge ? copy.confirmPurge(companyName) : copy.confirmSoft(companyName))) return;
    setBusy(true);
    setNote(null);
    try {
      const res = await adminCall(`/api/admin/tenants/${licenseId}/delete`, { purge });
      if (!res.ok) {
        if (res.status === 401) return;
        const why = res.status === 403 ? copy.purgeNeedsBreakGlass : res.reason;
        setNote({ text: why ? `${copy.failed} · ${ADMIN.tenant.actions.reason(why)}` : copy.failed, tone: "error" });
        return;
      }
      if (purge) {
        const rows = (res.body as { rows?: Record<string, number> } | null)?.rows ?? {};
        const total = Object.values(rows).reduce((sum, n) => sum + n, 0);
        setNote({ text: copy.purged(total), tone: "ok" });
        router.push("/admin");
        return;
      }
      setNote({ text: copy.deleted, tone: "ok" });
      setTyped("");
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pa-card" style={{ borderColor: "#7f2e2e" }}>
      <h2>{copy.title}</h2>
      <p className="pa-muted" style={{ fontSize: 13 }}>{copy.intro}</p>
      <div className="pa-filters" style={{ flexWrap: "wrap", gap: 12, marginBottom: 0 }}>
        <label className="pa-field">
          {copy.typeCode(code || "—")}
          <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={copy.codePlaceholder} disabled={busy} autoComplete="off" />
        </label>
        <label className="pa-field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} disabled={busy} />
          {copy.purge}
        </label>
        <button type="button" className="pa-btn pa-btn-danger" disabled={busy || !armed} onClick={() => void remove()}>
          {busy ? copy.working : purge ? copy.buttonPurge : copy.button}
        </button>
      </div>
      {note && <p className={`pa-note pa-note-${note.tone}`} role="status">{note.text}</p>}
    </section>
  );
}
