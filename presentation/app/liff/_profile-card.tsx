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
  const [language, setLanguage] = useState<string>("th");
  const [dateFormat, setDateFormat] = useState<string>("");
  const [timezone, setTimezone] = useState<string>("Asia/Bangkok");
  const [signature, setSignature] = useState<string | null>(null);

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
        // Phase 16.3: the language the system replies in, per person.
        const prefs = await fetch(`/api/liff/${audience}/display-preferences`, {
          headers: headers(),
        });
        if (prefs.ok && !cancelled) {
          const p = (await prefs.json()) as { language?: string | null; date_format?: string | null; timezone?: string | null };
          setLanguage(p.language === "en" ? "en" : "th");
          setDateFormat(p.date_format && p.date_format !== "dd/mm/yyyy" ? p.date_format : (p.date_format ?? ""));
          setTimezone(p.timezone || "Asia/Bangkok");
        }
        // 13.5: whether a signature is on file (printed on approved reports).
        const sig = await fetch(`/api/liff/${audience}/signature`, { headers: headers() });
        if (sig.ok && !cancelled) {
          const body = (await sig.json()) as { url?: string | null };
          setSignature(body.url ?? null);
        }
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

  async function savePref(fields: Record<string, string>) {
    try {
      const response = await fetch(`/api/liff/${audience}/display-preferences`, {
        method: "PUT",
        headers: headers(),
        body: JSON.stringify(fields),
      });
      if (!response.ok) throw new Error(String(response.status));
      setNote({ text: copy.languageSaved, tone: "ok" });
    } catch {
      setNote({ text: copy.saveFailed, tone: "error" });
    }
  }

  async function chooseLanguage(next: string) {
    setLanguage(next);
    try {
      const response = await fetch(`/api/liff/${audience}/display-preferences`, {
        method: "PUT",
        headers: headers(),
        body: JSON.stringify({ language: next }),
      });
      if (!response.ok) throw new Error(String(response.status));
      setNote({ text: copy.languageSaved, tone: "ok" });
    } catch {
      setNote({ text: copy.saveFailed, tone: "error" });
    }
  }

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
      {note && (
        <p className="card-meta" data-tone={note.tone} role="status">
          {note.text}
        </p>
      )}
      {!editing && profile && (
        <dl className="fields">
          {shopName && (
            // Which shop this person belongs to — a row like the others,
            // worded for the OA: a technician is the shop's, a customer
            // is a customer of it (owner, 3 Sep: "ลูกค้าของ" on the
            // technician home was wrong, and the loose line sat off-grid).
            <FieldRow label={audience === "technician" ? copy.shopTechnician : copy.shopCustomer}>
              {shopName}
            </FieldRow>
          )}
          <FieldRow label={copy.name} empty={!fullName}>
            {fullName || copy.notSet}
          </FieldRow>
          <FieldRow label={copy.phone} empty={!profile.phone}>
            {profile.phone || copy.notSet}
          </FieldRow>
          <FieldRow label={copy.address} empty={!profile.address}>
            {profile.address || copy.notSet}
          </FieldRow>
          <FieldRow label={copy.signature}>
            <span>
              {signature ? copy.signatureSet : copy.signatureNone}
              {" · "}
              <a href={`/liff/${audience}/signature`}>{copy.signatureEdit}</a>
            </span>
          </FieldRow>
          <FieldRow label={copy.language}>
            {(id) => (
              <select id={id} value={language} onChange={(e) => void chooseLanguage(e.target.value)}>
                <option value="th">{copy.languageTh}</option>
                <option value="en">{copy.languageEn}</option>
              </select>
            )}
          </FieldRow>
          <FieldRow label={copy.dateFormat}>
            {(id) => (
              <select
                id={id}
                value={dateFormat}
                onChange={(e) => {
                  setDateFormat(e.target.value);
                  void savePref({ date_format: e.target.value || "dd/mm/yyyy" });
                }}
              >
                <option value="">{copy.dateFormatDefault}</option>
                <option value="dd/mm/yyyy">dd/mm/yyyy</option>
                <option value="mm/dd/yyyy">mm/dd/yyyy</option>
                <option value="yyyy-mm-dd">yyyy-mm-dd</option>
              </select>
            )}
          </FieldRow>
          <FieldRow label={copy.timezone}>
            {(id) => (
              <select
                id={id}
                value={timezone}
                onChange={(e) => {
                  setTimezone(e.target.value);
                  void savePref({ timezone: e.target.value });
                }}
              >
                {["Asia/Bangkok", "Asia/Kuala_Lumpur", "Asia/Singapore", "Asia/Jakarta", "Asia/Tokyo", "UTC"].map((z) => (
                  <option key={z} value={z}>{z}</option>
                ))}
              </select>
            )}
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
