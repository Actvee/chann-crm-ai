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
  // 16.4: whether a customer who links in LINE joins the list at once.
  const [autoAccept, setAutoAccept] = useState<boolean | null>(null);
  // Owner, 4 Sep: the chat timers are the company's to set here and in chat.
  const [chatSla, setChatSla] = useState("");
  const [chatTimeout, setChatTimeout] = useState("");
  const [leadCleanupDays, setLeadCleanupDays] = useState("0");
  const [policySaving, setPolicySaving] = useState(false);

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
      else void loadAutoAccept(session.token, session.memberships[0].license_id);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say]);

  async function loadAutoAccept(currentToken: string, license: string) {
    const response = await fetch(`/api/phase2/licenses/${license}/settings`, {
      headers: proxyHeaders(currentToken, license),
    });
    if (!response.ok) return;  // no setting.manage: the switch stays hidden
    const rows = (await response.json()) as { setting_key: string; setting_value: unknown }[];
    const row = rows.find((r) => r.setting_key === "auto_accept_new_customers");
    setAutoAccept(row ? row.setting_value === true || String(row.setting_value).toLowerCase() === "true" : false);
    const sla = rows.find((r) => r.setting_key === "chat_sla_minutes" || r.setting_key === "chat_sla");
    const quiet = rows.find((r) => r.setting_key === "chat_timeout_minutes" || r.setting_key === "session_timeout");
    setChatSla(sla ? String(sla.setting_value) : "15");
    setChatTimeout(quiet ? String(quiet.setting_value) : "60");
    const cleanup = rows.find((r) => r.setting_key === "lead_auto_archive_days");
    setLeadCleanupDays(cleanup ? String(cleanup.setting_value) : "0");
  }

  async function saveChatPolicy() {
    const sla = Number(chatSla);
    const quiet = Number(chatTimeout);
    const cleanup = Number(leadCleanupDays || 0);
    if (!Number.isInteger(sla) || sla < 1 || sla > 1440 || !Number.isInteger(quiet) || quiet < 1 || quiet > 1440) {
      say(c.chatPolicyInvalid, "error");
      return;
    }
    if (!Number.isInteger(cleanup) || cleanup < 0 || cleanup > 3650) {
      say(c.leadCleanupInvalid, "error");
      return;
    }
    setPolicySaving(true);
    try {
      for (const [key, value] of [["chat_sla_minutes", sla], ["chat_timeout_minutes", quiet], ["lead_auto_archive_days", cleanup]] as const) {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/settings/${key}`, {
          method: "PUT",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({ setting_value: value }),
        });
        if (!response.ok) throw new Error(String(response.status));
      }
      say(c.autoAcceptSaved, "ok");
    } catch {
      say(t.common.error, "error");
    } finally {
      setPolicySaving(false);
    }
  }

  async function saveAutoAccept(next: boolean) {
    setAutoAccept(next);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/settings/auto_accept_new_customers`, {
        method: "PUT",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ setting_value: next }),
      });
      if (!response.ok) throw new Error(String(response.status));
      say(c.autoAcceptSaved, "ok");
    } catch {
      say(t.common.error, "error");
    }
  }

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

      {autoAccept !== null && (
        <section className="section" style={{ marginTop: 16 }}>
          <label className="field">
            <span>
              <input
                type="checkbox"
                checked={autoAccept}
                onChange={(e) => void saveAutoAccept(e.target.checked)}
                style={{ marginRight: 8 }}
              />
              {c.autoAccept}
            </span>
            <span className="hint">{c.autoAcceptHint}</span>
          </label>
        </section>
      )}

      {autoAccept !== null && (
        <section className="section" style={{ marginTop: 16 }}>
          <div className="section-head">
            <h2>{c.chatPolicy}</h2>
          </div>
          <dl className="fields">
            <div className="field">
              <label htmlFor="chat-sla">{c.chatSla}</label>
              <input
                id="chat-sla"
                type="number"
                inputMode="numeric"
                min={1}
                max={1440}
                value={chatSla}
                onChange={(e) => setChatSla(e.target.value)}
                aria-describedby="chat-sla-hint"
              />
              <span id="chat-sla-hint" className="hint">{c.chatSlaHint}</span>
            </div>
            <div className="field">
              <label htmlFor="chat-timeout">{c.chatTimeout}</label>
              <input
                id="chat-timeout"
                type="number"
                inputMode="numeric"
                min={1}
                max={1440}
                value={chatTimeout}
                onChange={(e) => setChatTimeout(e.target.value)}
                aria-describedby="chat-timeout-hint"
              />
              <span id="chat-timeout-hint" className="hint">{c.chatTimeoutHint}</span>
            </div>
            <div className="field">
              <label htmlFor="lead-cleanup-days">{c.leadCleanup}</label>
              <input
                id="lead-cleanup-days"
                type="number"
                inputMode="numeric"
                min={0}
                max={3650}
                value={leadCleanupDays}
                onChange={(e) => setLeadCleanupDays(e.target.value)}
                aria-describedby="lead-cleanup-hint"
              />
              <span id="lead-cleanup-hint" className="hint">{c.leadCleanupHint}</span>
            </div>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={policySaving}
                onClick={() => void saveChatPolicy()}
              >
                {policySaving ? t.dashboard.related.saving : c.chatPolicySave}
              </button>
            </div>
          </dl>
        </section>
      )}
    </AppShell>
  );
}
