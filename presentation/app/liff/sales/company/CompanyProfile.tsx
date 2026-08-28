"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AppShell, CompanyPicker } from "../_components";
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
  const [status, setStatus] = useState("กำลังเปิด…");
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
          ? "ต้องมีสิทธิ์ setting.manage จึงจะแก้ข้อมูลบริษัทได้"
          : `โหลดข้อมูลบริษัทไม่สำเร็จ (${response.status})`,
      );
    }
    applyProfile((await response.json()) as Profile);
    say("");
  }, [applyProfile, licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : "โหลดข้อมูลบริษัทไม่สำเร็จ", "error"),
    );
  }, [licenseId, load, say, token]);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      setToken(session.token);
      setMemberships(session.memberships);
      setLicenseId(session.memberships[0]?.license_id ?? "");
      if (!session.memberships.length) say("ยังไม่พบบริษัทที่ผูกไว้", "error");
    } catch (error) {
      say(error instanceof Error ? error.message : "เปิดหน้านี้ไม่สำเร็จ", "error");
    }
  }, [liffId, say]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const digits = taxId.replace(/\D/g, "");
    if (digits && digits.length !== 13) {
      say("เลขผู้เสียภาษีต้องเป็นตัวเลข 13 หลักพอดี", "error");
      return;
    }

    setSaving(true);
    say("กำลังบันทึก…");
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
            ? "ข้อมูลไม่ถูกต้อง ตรวจเลขผู้เสียภาษี (13 หลัก) และอัตราภาษี (0–100)"
            : `บันทึกไม่สำเร็จ (${response.status})`,
          "error",
        );
        return;
      }
      applyProfile((await response.json()) as Profile);
      say("บันทึกข้อมูลบริษัทแล้ว", "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : "บันทึกไม่สำเร็จ", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell
      title="ข้อมูลบริษัท"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say("โหลด LIFF ไม่สำเร็จ", "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      {profile && (
        <div className="callout" data-tone={profile.is_document_ready ? "ok" : "warn"} role="status">
          <span className="dot" />
          <span>
            {profile.is_document_ready
              ? "ข้อมูลครบ ออกใบเสนอราคาได้แล้ว"
              : `ยังออกเอกสารไม่ได้ ต้องกรอก ${profile.missing_for_documents
                  .map((field) => FIELD_LABELS[field] ?? field)
                  .join(" และ ")} ก่อน`}
          </span>
        </div>
      )}

      <form onSubmit={save}>
        <label className="field">
          <span>ชื่อร้าน</span>
          <input value={profile?.company_name ?? ""} disabled />
          <span className="hint">ชื่อที่ใช้ในแชทและหน้าร้าน ไม่ใช่ชื่อบนเอกสาร</span>
        </label>

        <label className="field">
          <span>ชื่อนิติบุคคล</span>
          <input
            value={legalName}
            onChange={(event) => setLegalName(event.target.value)}
            placeholder="บริษัท ตัวอย่าง จำกัด"
          />
          <span className="hint">ชื่อที่จะพิมพ์บนใบเสนอราคา</span>
        </label>

        <label className="field field-mono">
          <span>เลขผู้เสียภาษี · จำเป็น</span>
          <input
            value={taxId}
            onChange={(event) => setTaxId(event.target.value)}
            inputMode="numeric"
            placeholder="0105558123456"
          />
          <span className="hint">13 หลัก</span>
        </label>

        <label className="field">
          <span>ที่อยู่บริษัท · จำเป็น</span>
          <textarea
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            rows={3}
            placeholder="99/1 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพฯ 10110"
          />
        </label>

        <label className="field">
          <span>เบอร์โทรบริษัท</span>
          <input value={phone} onChange={(event) => setPhone(event.target.value)} inputMode="tel" />
        </label>

        <label className="field">
          <span>อีเมลบริษัท</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>

        <fieldset className="group">
          <legend>ภาษีมูลค่าเพิ่ม</legend>
          <label className="check">
            <input
              type="checkbox"
              checked={vatRegistered}
              onChange={(event) => setVatRegistered(event.target.checked)}
            />
            บริษัทนี้จดทะเบียนภาษีมูลค่าเพิ่ม
          </label>
          {vatRegistered && (
            <label className="field field-mono" style={{ marginTop: 12, marginBottom: 0 }}>
              <span>อัตรา (%)</span>
              <input
                value={vatPercent}
                onChange={(event) => setVatPercent(event.target.value)}
                inputMode="decimal"
                placeholder="7"
              />
            </label>
          )}
          <p className="hint" style={{ marginTop: 10 }}>
            ถ้าไม่ได้จด VAT เอกสารจะไม่แสดงบรรทัดภาษีเลย ซึ่งต่างจากการตั้งอัตราเป็น 0%
          </p>
        </fieldset>

        <button type="submit" className="btn" data-variant="primary" disabled={!licenseId || saving}>
          {saving ? "กำลังบันทึก…" : "บันทึก"}
        </button>
      </form>
    </AppShell>
  );
}
