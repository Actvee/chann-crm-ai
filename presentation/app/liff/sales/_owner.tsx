"use client";

import { useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Sheet } from "../_sheet";
import { FieldRow } from "../_field-row";
import { proxyHeaders } from "./_lib";
import { useSalesText } from "./_strings";

/**
 * Handing a customer or a deal to a colleague (round 20V).
 *
 * PATCH .../customers/{id}/owner and .../deals/{id}/owner have existed
 * since 6 Sep 2026 behind reassign_records, and nothing on any screen
 * called them: a lead stayed with whoever typed it in, forever (owner's
 * gap list, 21 ก.ย. 2569: "ทำได้ในโค้ด แต่คนหาไม่เจอ"). Three pieces,
 * shared by the customer and deal detail pages and both lists:
 *
 *  - `useSalesMembers` — the active sales-side roster, fetched once;
 *  - `OwnerControl` — the "เจ้าของ" row on a record: the current owner by
 *    name and a native select of colleagues (a phone's own picker beats a
 *    custom dropdown on a 44px row);
 *  - `OwnerSheet` — the same choice for several rows at once, in a sheet
 *    with one confirming button that names how many rows it touches.
 */
export type SalesMember = {
  id: string;
  chann_uid: string;
  role: string;
  status: string;
  channel?: string;
  display_name?: string | null;
};

export function memberLabel(member: SalesMember | undefined | null, fallback: string): string {
  return member?.display_name?.trim() || member?.chann_uid || fallback;
}

/** The active members on the sales side — the pool a record can go to. */
export function useSalesMembers(token: string, licenseId: string, enabled: boolean): SalesMember[] {
  const [members, setMembers] = useState<SalesMember[]>([]);
  useEffect(() => {
    if (!enabled || !token || !licenseId) return;
    let live = true;
    void (async () => {
      try {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/members`, {
          headers: proxyHeaders(token, licenseId),
        });
        if (!response.ok) return;
        const rows = (await response.json()) as SalesMember[];
        if (live) {
          setMembers(
            rows.filter((m) => (m.status ?? "active") === "active" && (m.channel ?? "sales") !== "technician"),
          );
        }
      } catch {
        // The control simply does not render; the rest of the page works.
      }
    })();
    return () => {
      live = false;
    };
  }, [enabled, token, licenseId]);
  return members;
}

/**
 * One record's owner. `onChange` is called with the chosen member AFTER
 * the page has asked its own confirm dialog — the dialog belongs to the
 * page because only it knows the record's name and code.
 */
export function OwnerControl({
  members,
  ownerId,
  busy,
  onPick,
}: {
  members: SalesMember[];
  ownerId: string | null | undefined;
  busy: boolean;
  onPick: (member: SalesMember) => void;
}) {
  const { t } = useLanguage();
  const s = useSalesText();
  const current = members.find((m) => m.id === ownerId);
  return (
    <section className="section" style={{ margin: "0 0 14px" }}>
      <div className="section-head">
        <h2>{s.transfer.ownerTitle}</h2>
      </div>
      <dl className="fields">
        <FieldRow label={s.transfer.currentOwner}>
          <strong>{ownerId ? memberLabel(current, s.transfer.someoneElse) : s.transfer.nobody}</strong>
        </FieldRow>
        {members.length > 0 && (
          <FieldRow label={s.transfer.handTo}>
            {(id) => (
              <select
                id={id}
                value=""
                disabled={busy}
                aria-describedby={`${id}-hint`}
                onChange={(event) => {
                  const picked = members.find((m) => m.id === event.target.value);
                  if (picked) onPick(picked);
                }}
              >
                <option value="">{s.transfer.pickColleague}</option>
                {members
                  .filter((m) => m.id !== ownerId)
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {memberLabel(m, m.chann_uid)}{m.role ? ` · ${m.role}` : ""}
                    </option>
                  ))}
              </select>
            )}
          </FieldRow>
        )}
      </dl>
      <p className="hint">{busy ? t.dashboard.saving : s.transfer.hint}</p>
    </section>
  );
}

/** The bulk form: pick one colleague for every chosen row. */
export function OwnerSheet({
  open,
  count,
  members,
  busy,
  onClose,
  onConfirm,
}: {
  open: boolean;
  count: number;
  members: SalesMember[];
  busy: boolean;
  onClose: () => void;
  onConfirm: (member: SalesMember) => void;
}) {
  const { t } = useLanguage();
  const s = useSalesText();
  const [picked, setPicked] = useState("");
  const chosen = members.find((m) => m.id === picked);
  return (
    <Sheet open={open} title={s.transfer.manyTitle.replace("{n}", String(count))} onClose={onClose}>
      <dl className="fields">
        <FieldRow label={s.transfer.handTo}>
          {(id) => (
            <select id={id} value={picked} disabled={busy} onChange={(event) => setPicked(event.target.value)}>
              <option value="">{s.transfer.pickColleague}</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {memberLabel(m, m.chann_uid)}{m.role ? ` · ${m.role}` : ""}
                </option>
              ))}
            </select>
          )}
        </FieldRow>
        <p className="hint">{s.transfer.manyHint}</p>
        <div className="actions">
          <button type="button" className="btn" data-variant="quiet" onClick={onClose} disabled={busy}>
            {t.common.cancel}
          </button>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            disabled={busy || !chosen}
            onClick={() => chosen && onConfirm(chosen)}
          >
            {busy
              ? t.dashboard.saving
              : s.transfer.manyConfirm.replace("{n}", String(count)).replace("{name}", chosen ? memberLabel(chosen, "") : "…")}
          </button>
        </div>
      </dl>
    </Sheet>
  );
}
