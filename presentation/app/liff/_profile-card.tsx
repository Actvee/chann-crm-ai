"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { FieldRow } from "./_field-row";

type Profile = {
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
  address?: string | null;
};

/**
 * The signed-in person's own details — name, phone, address — read and
 * edited in place. One component for the customer and technician homes
 * because both OAs let the person keep their own record (Phase 8's
 * PROFILE_ELIGIBLE_ROLES), and the chat already does ("แก้เบอร์เป็น
 * 08x"); the owner asked for the same on screen (3 Sep).
 *
 * Collapsed to a one-line summary until "แก้ไข" is tapped: the details
 * are read a hundred times for every time they change.
 */
export function ProfileCard({
  token,
  audience,
  shopName,
}: {
  token: string;
  audience: "customer" | "technician";
  shopName?: string | null;
}) {
  const { t } = useLanguage();
  const copy = t.dashboard.profile;
  const [profile, setProfile] = useState<Profile | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Profile>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "ok" | "error" } | null>(null);

  const headers = useCallback(
    () => ({ "X-Liff-ID-Token": token, "Content-Type": "application/json" }),
    [token],
  );

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`/api/liff/${audience}/profile`, {
          headers: headers(),
        });
        if (!response.ok) throw new Error(String(response.status));
        const row = (await response.json()) as Profile;
        if (!cancelled) setProfile(row);
      } catch {
        if (!cancelled) setNote({ text: copy.loadFailed, tone: "error" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, audience, headers, copy.loadFailed]);

  async function save() {
    setBusy(true);
    setNote(null);
    try {
      const response = await fetch(`/api/liff/${audience}/profile`, {
        method: "PATCH",
        headers: headers(),
        body: JSON.stringify({
          first_name: draft.first_name ?? "",
          last_name: draft.last_name ?? "",
          phone: draft.phone ?? "",
          address: draft.address ?? "",
        }),
      });
      if (!response.ok) throw new Error(String(response.status));
      setProfile((await response.json()) as Profile);
      setEditing(false);
      setNote({ text: copy.saved, tone: "ok" });
    } catch {
      setNote({ text: copy.saveFailed, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  const fullName = [profile?.first_name, profile?.last_name].filter(Boolean).join(" ");

  return (
    <section className="section">
      <div className="section-head">
        <h2>{copy.title}</h2>
        {!editing && profile && (
          <button
            type="button"
            className="btn"
            data-variant="quiet"
            onClick={() => {
              setDraft({ ...profile });
              setEditing(true);
              setNote(null);
            }}
          >
            {copy.edit}
          </button>
        )}
      </div>
      {shopName && (
        // Which shop this person belongs to — the one fact a customer
        // asked "ลูกค้าร้านไหน" could not see anywhere on the old page.
        <p className="card-meta">{copy.shopOf.replace("{shop}", shopName)}</p>
      )}
      {note && (
        <p className="card-meta" data-tone={note.tone} role="status">
          {note.text}
        </p>
      )}
      {!editing && profile && (
        <dl className="fields">
          <FieldRow label={copy.name} empty={!fullName}>
            {fullName || copy.notSet}
          </FieldRow>
          <FieldRow label={copy.phone} empty={!profile.phone}>
            {profile.phone || copy.notSet}
          </FieldRow>
          <FieldRow label={copy.address} empty={!profile.address}>
            {profile.address || copy.notSet}
          </FieldRow>
        </dl>
      )}
      {editing && (
        <dl className="fields">
          <FieldRow label={copy.firstName}>
            {(id) => (
              <input
                id={id}
                value={draft.first_name ?? ""}
                autoComplete="given-name"
                onChange={(e) => setDraft({ ...draft, first_name: e.target.value })}
              />
            )}
          </FieldRow>
          <FieldRow label={copy.lastName}>
            {(id) => (
              <input
                id={id}
                value={draft.last_name ?? ""}
                autoComplete="family-name"
                onChange={(e) => setDraft({ ...draft, last_name: e.target.value })}
              />
            )}
          </FieldRow>
          <FieldRow label={copy.phone}>
            {(id) => (
              <input
                id={id}
                type="tel"
                inputMode="tel"
                value={draft.phone ?? ""}
                autoComplete="tel"
                onChange={(e) => setDraft({ ...draft, phone: e.target.value })}
              />
            )}
          </FieldRow>
          <FieldRow label={copy.address}>
            {(id) => (
              <textarea
                id={id}
                rows={2}
                value={draft.address ?? ""}
                autoComplete="street-address"
                onChange={(e) => setDraft({ ...draft, address: e.target.value })}
              />
            )}
          </FieldRow>
          <div className="actions">
            <button
              type="button"
              className="btn"
              data-variant="quiet"
              disabled={busy}
              onClick={() => setEditing(false)}
            >
              {t.dashboard.related.cancelForm}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              disabled={busy}
              onClick={() => void save()}
            >
              {busy ? t.dashboard.related.saving : copy.save}
            </button>
          </div>
        </dl>
      )}
    </section>
  );
}
