"use client";

import Script from "next/script";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";

type Membership = { license_id: string; license_code: string; company_name: string };

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

type LiffApi = {
  init(config: { liffId: string; withLoginOnExternalBrowser: boolean }): Promise<void>;
  isLoggedIn(): boolean;
  login(): void;
  getIDToken(): string | null;
};

function getLiff(): LiffApi | undefined {
  return (window as Window & { liff?: LiffApi }).liff;
}

const FIELD_LABELS: Record<string, string> = {
  legal_name: "ชื่อนิติบุคคล",
  tax_id: "เลขผู้เสียภาษี",
  company_address: "ที่อยู่บริษัท",
  company_phone: "เบอร์โทรบริษัท",
  company_email: "อีเมลบริษัท",
  vat_rate: "ภาษีมูลค่าเพิ่ม",
};

export default function CompanyProfile({ liffId }: { liffId: string }) {
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState("กำลังเริ่ม LIFF…");

  const [legalName, setLegalName] = useState("");
  const [taxId, setTaxId] = useState("");
  const [address, setAddress] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  // Held as a string, not a number: an empty box has to stay distinguishable
  // from a real 0, because "not VAT-registered" and "registered at 0%" are
  // different states and get stored differently (null vs 0).
  const [vatPercent, setVatPercent] = useState("");
  const [vatRegistered, setVatRegistered] = useState(true);

  const headers = useCallback(
    () => ({
      "Content-Type": "application/json",
      "X-Liff-ID-Token": token,
      "X-Liff-Audience": "sales",
      "X-License-Id": licenseId,
    }),
    [token, licenseId],
  );

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

  const loadProfile = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/company-profile`, {
      headers: headers(),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? "คุณไม่มีสิทธิ์ setting.manage สำหรับแก้ข้อมูลบริษัท"
          : `โหลดข้อมูลบริษัทไม่สำเร็จ (${response.status})`,
      );
    }
    applyProfile((await response.json()) as Profile);
    setStatus("");
  }, [applyProfile, headers, licenseId, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void loadProfile().catch((error: unknown) =>
      setStatus(error instanceof Error ? error.message : "โหลดข้อมูลบริษัทไม่สำเร็จ"),
    );
  }, [licenseId, loadProfile, token]);

  const initialize = useCallback(async () => {
    const liff = getLiff();
    if (!liffId || !liff) {
      setStatus("NEXT_PUBLIC_LIFF_SALES_ID is REQUIRED_NOT_CONFIGURED");
      return;
    }
    try {
      await liff.init({ liffId, withLoginOnExternalBrowser: true });
      if (!liff.isLoggedIn()) {
        liff.login();
        return;
      }
      const idToken = liff.getIDToken();
      if (!idToken) throw new Error("LIFF did not return an ID token");
      const response = await fetch("/api/liff/sales/me", {
        headers: { "X-Liff-ID-Token": idToken },
      });
      if (!response.ok) throw new Error(`authentication failed (${response.status})`);
      const me = (await response.json()) as { memberships: Membership[] };
      setToken(idToken);
      setMemberships(me.memberships);
      setLicenseId(me.memberships[0]?.license_id ?? "");
      if (!me.memberships.length) setStatus("ยังไม่พบบริษัทที่ผูกไว้");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "LIFF initialization failed");
    }
  }, [liffId]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const digits = taxId.replace(/\D/g, "");
    if (digits && digits.length !== 13) {
      setStatus("เลขผู้เสียภาษีต้องเป็นตัวเลข 13 หลักพอดี");
      return;
    }

    // Sending null for vat_rate_percent explicitly clears it, which is how
    // "not VAT-registered" is expressed — distinct from omitting the field.
    const body = {
      legal_name: legalName.trim() || null,
      tax_id: digits || null,
      company_address: address.trim() || null,
      company_phone: phone.trim() || null,
      company_email: email.trim() || null,
      vat_rate_percent: vatRegistered && vatPercent !== "" ? Number(vatPercent) : null,
    };

    const response = await fetch(`/api/phase2/licenses/${licenseId}/company-profile`, {
      method: "PATCH",
      headers: headers(),
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      setStatus(
        response.status === 422
          ? "ข้อมูลไม่ถูกต้อง — ตรวจเลขผู้เสียภาษี (13 หลัก) และอัตราภาษี (0–100)"
          : `บันทึกไม่สำเร็จ (${response.status})`,
      );
      return;
    }
    applyProfile((await response.json()) as Profile);
    setStatus("บันทึกข้อมูลบริษัทเรียบร้อยแล้ว");
  }

  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: 24 }}>
      <Script
        src="https://static.line-scdn.net/liff/edge/versions/2.29.2/sdk.js"
        strategy="afterInteractive"
        onReady={() => void initialize()}
        onError={() => setStatus("LIFF SDK load failed")}
      />
      <h1>ข้อมูลบริษัทสำหรับออกเอกสาร</h1>
      <LanguageSwitcher />
      <p aria-live="polite">{status}</p>

      {memberships.length > 1 && (
        <label>
          บริษัท
          <select value={licenseId} onChange={(event) => setLicenseId(event.target.value)}>
            {memberships.map((membership) => (
              <option key={membership.license_id} value={membership.license_id}>
                {membership.company_name} ({membership.license_code})
              </option>
            ))}
          </select>
        </label>
      )}

      {profile && (
        <p
          role="status"
          style={{
            border: "1px solid",
            borderColor: profile.is_document_ready ? "#2e7d32" : "#c62828",
            color: profile.is_document_ready ? "#2e7d32" : "#c62828",
            padding: 12,
            borderRadius: 6,
          }}
        >
          {profile.is_document_ready
            ? "ข้อมูลครบ พร้อมออกใบเสนอราคาแล้ว"
            : `ยังออกเอกสารไม่ได้ — ยังขาด: ${profile.missing_for_documents
                .map((field) => FIELD_LABELS[field] ?? field)
                .join(", ")}`}
        </p>
      )}

      <form onSubmit={save} style={{ display: "grid", gap: 12, marginTop: 16 }}>
        <label>
          ชื่อร้าน (ใช้ในแชท ไม่ใช่ชื่อบนเอกสาร)
          <input value={profile?.company_name ?? ""} disabled />
        </label>
        <label>
          ชื่อนิติบุคคล (ชื่อที่จะแสดงบนเอกสาร)
          <input
            value={legalName}
            onChange={(event) => setLegalName(event.target.value)}
            placeholder="บริษัท ตัวอย่าง จำกัด"
          />
        </label>
        <label>
          เลขผู้เสียภาษี (13 หลัก) *
          <input
            value={taxId}
            onChange={(event) => setTaxId(event.target.value)}
            inputMode="numeric"
            placeholder="0105558123456"
          />
        </label>
        <label>
          ที่อยู่บริษัท *
          <textarea
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            rows={3}
            placeholder="99/1 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพฯ 10110"
          />
        </label>
        <label>
          เบอร์โทรบริษัท
          <input value={phone} onChange={(event) => setPhone(event.target.value)} />
        </label>
        <label>
          อีเมลบริษัท
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>

        <fieldset style={{ border: "1px solid #ddd", padding: 12 }}>
          <legend>ภาษีมูลค่าเพิ่ม</legend>
          <label style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={vatRegistered}
              onChange={(event) => setVatRegistered(event.target.checked)}
            />{" "}
            บริษัทนี้จดทะเบียนภาษีมูลค่าเพิ่ม
          </label>
          {vatRegistered && (
            <label>
              อัตรา (%)
              <input
                value={vatPercent}
                onChange={(event) => setVatPercent(event.target.value)}
                inputMode="decimal"
                placeholder="7"
              />
            </label>
          )}
          <p style={{ fontSize: 13, color: "#666", margin: "8px 0 0" }}>
            ถ้าไม่ได้จด VAT เอกสารจะไม่แสดงบรรทัดภาษีเลย ซึ่งต่างจากการตั้งอัตราเป็น 0%
          </p>
        </fieldset>

        <button type="submit" disabled={!licenseId}>
          บันทึก
        </button>
        <p style={{ fontSize: 13, color: "#666" }}>
          * จำเป็นต้องกรอกก่อนออกใบเสนอราคา
        </p>
      </form>
    </main>
  );
}
