"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AppShell, CompanyPicker } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Membership, initLiffSession, proxyHeaders } from "../_lib";

type Profile = {
  legal_name: string | null;
  company_name: string;
  tax_id: string | null;
  company_address: string | null;
  company_phone: string | null;
  company_email: string | null;
  vat_rate: string | null;
  is_document_ready: boolean;
  missing_for_documents: string[];
};

export default function CompanyProfile({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const c = t.dashboard.companyProfile;
  const fieldLabel = (field: string) =>
    ({
      legal_name: c.legalName,
      tax_id: c.taxId,
      company_address: c.address,
      company_phone: c.phone,
      company_email: c.email,
      vat_rate: c.vat,
    })[field] ?? field;
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [saving, setSaving] = useState(false);

  const [legalName, setLegalName] = useState("");
  const [taxId, setTaxId] = useState("");
  const [address, setAddress] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  // Held as a string so an empty box stays distinguishable from a real 0:
  // "not VAT-registered" and "registered at 0%" are different states and
  // are stored differently (null vs 0).
  const [vatPercent, setVatPercent] = useState("");
  const [vatRegistered, setVatRegistered] = useState(true);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const applyProfile = useCallback((data: Profile) => {
    setProfile(data);
    setLegalName(data.legal_name ?? "");
    setTaxId(data.tax_id ?? "");
    setAddress(data.company_address ?? "");
    setPhone(data.company_phone ?? "");
    setEmail(data.company_email ?? "");
    if (data.vat_rate === null || data.vat_rate === "") {
      setVatRegistered(false);
      setVatPercent("");
    } else {
      setVatRegistered(true);
      setVatPercent(String(Number(data.vat_rate) * 100));
    }
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/company-profile`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? c.settingDenied
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    applyProfile((await response.json()) as Profile);
    say("");
  }, [applyProfile, licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [licenseId, load, say, token]);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      setToken(session.token);
      setMemberships(session.memberships);
      setLicenseId(session.memberships[0]?.license_id ?? "");
      if (!session.memberships.length) say(t.liff.noCompany, "error");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const digits = taxId.replace(/\D/g, "");
    if (digits && digits.length !== 13) {
      say(c.taxIdInvalid, "error");
      return;
    }

    setSaving(true);
    say(t.dashboard.saving);
    try {
      // An explicit null clears a field, which is how "no longer
      // VAT-registered" is expressed — distinct from omitting the key.
      const response = await fetch(`/api/phase2/licenses/${licenseId}/company-profile`, {
        method: "PATCH",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({
          legal_name: legalName.trim() || null,
          tax_id: digits || null,
          company_address: address.trim() || null,
          company_phone: phone.trim() || null,
          company_email: email.trim() || null,
          vat_rate_percent: vatRegistered && vatPercent !== "" ? Number(vatPercent) : null,
        }),
      });
      if (!response.ok) {
        say(
          response.status === 422
            ? c.invalid
            : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      applyProfile((await response.json()) as Profile);
      say(t.dashboard.saved, "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell
      title={t.dashboard.companyTitle}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      {profile && (
        <div className="callout" data-tone={profile.is_document_ready ? "ok" : "warn"} role="status">
          <span className="dot" />
          <span>
            {profile.is_document_ready
              ? c.ready
              : c.missing.replace(
                  "{fields}",
                  profile.missing_for_documents.map(fieldLabel).join(", "),
                )}
          </span>
        </div>
      )}

      <form onSubmit={save}>
        <label className="field">
          <span>{c.shopName}</span>
          <input value={profile?.company_name ?? ""} disabled />
          <span className="hint">{c.shopNameHint}</span>
        </label>

        <label className="field">
          <span>{c.legalName}</span>
          <input
            value={legalName}
            onChange={(event) => setLegalName(event.target.value)}
            placeholder="Example Co., Ltd."
          />
          <span className="hint">{c.legalNameHint}</span>
        </label>

        <label className="field field-mono">
          <span>{`${c.taxId} · ${c.required}`}</span>
          <input
            value={taxId}
            onChange={(event) => setTaxId(event.target.value)}
            inputMode="numeric"
            placeholder="0105558123456"
          />
          <span className="hint">{c.taxIdHint}</span>
        </label>

        <label className="field">
          <span>{`${c.address} · ${c.required}`}</span>
          <textarea
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            rows={3}
            placeholder="99/1 Sukhumvit Rd, Bangkok 10110"
          />
        </label>

        <label className="field">
          <span>{c.phone}</span>
          <input value={phone} onChange={(event) => setPhone(event.target.value)} inputMode="tel" />
        </label>

        <label className="field">
          <span>{c.email}</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>

        <fieldset className="group">
          <legend>{c.vat}</legend>
          <label className="check">
            <input
              type="checkbox"
              checked={vatRegistered}
              onChange={(event) => setVatRegistered(event.target.checked)}
            />
            {c.vatRegistered}
          </label>
          {vatRegistered && (
            <label className="field field-mono" style={{ marginTop: 12, marginBottom: 0 }}>
              <span>{c.vatRate}</span>
              <input
                value={vatPercent}
                onChange={(event) => setVatPercent(event.target.value)}
                inputMode="decimal"
                placeholder="7"
              />
            </label>
          )}
          <p className="hint" style={{ marginTop: 10 }}>
            {c.vatNote}
          </p>
        </fieldset>

        <button type="submit" className="btn" data-variant="primary" disabled={!licenseId || saving}>
          {saving ? t.dashboard.saving : t.common.save}
        </button>
      </form>
    </AppShell>
  );
}
