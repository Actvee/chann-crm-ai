"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell, Badge, CompanyPicker, Count, Empty } from "../_components";
import { Membership, initLiffSession, proxyHeaders } from "../_lib";

type Quote = {
  id: string;
  quote_id: string;
  status: string;
  generated_document_id?: string | null;
};

const STATUS_LABELS: Record<string, string> = {
  draft: "ร่าง",
  sent: "ส่งแล้ว",
  accepted: "ตอบรับแล้ว",
  rejected: "ปฏิเสธ",
  expired: "หมดอายุ",
};

export default function QuoteList({ liffId }: { liffId: string }) {
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [status, setStatus] = useState("กำลังเปิด…");
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/quotes`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? "คุณไม่มีสิทธิ์ดูใบเสนอราคา"
          : `โหลดใบเสนอราคาไม่สำเร็จ (${response.status})`,
      );
    }
    setQuotes((await response.json()) as Quote[]);
    say("");
  }, [licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : "โหลดใบเสนอราคาไม่สำเร็จ", "error"),
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

  async function detail(response: Response): Promise<string> {
    try {
      return ((await response.clone().json()) as { detail?: string }).detail ?? "";
    } catch {
      return "";
    }
  }

  async function preview(quote: Quote) {
    setBusyId(quote.id);
    say(`กำลังสร้างตัวอย่าง ${quote.quote_id}…`);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quote.id}/pdf`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        const reason = await detail(response);
        say(
          response.status === 409
            ? `ยังออกเอกสารไม่ได้ ${reason} — กรอกข้อมูลบริษัทให้ครบก่อน`
            : `สร้างตัวอย่างไม่สำเร็จ (${response.status}) ${reason}`,
          "error",
        );
        return;
      }
      // Opened as a blob rather than as a link: the request needs LIFF auth
      // headers, which a plain anchor cannot send.
      const url = URL.createObjectURL(await response.blob());
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      say(`เปิดตัวอย่าง ${quote.quote_id} แล้ว`, "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : "สร้างตัวอย่างไม่สำเร็จ", "error");
    } finally {
      setBusyId("");
    }
  }

  async function issue(quote: Quote) {
    const already = Boolean(quote.generated_document_id);
    if (
      !window.confirm(
        already
          ? `${quote.quote_id} มีเอกสารที่ออกไปแล้ว\n\nการออกใหม่จะสร้างเอกสารอีกฉบับ ต้องการทำต่อหรือไม่`
          : `ออกเอกสารสำหรับ ${quote.quote_id}?\n\nเอกสารจะถูกเก็บถาวรพร้อมบันทึกหลักฐานว่าลูกค้าได้รับไฟล์ฉบับใด`,
      )
    ) {
      return;
    }
    setBusyId(quote.id);
    say(`กำลังออกเอกสาร ${quote.quote_id}…`);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quote.id}/issue?allow_reissue=${already}`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      const reason = await detail(response);
      if (!response.ok) {
        say(
          response.status === 409
            ? `ยังออกเอกสารไม่ได้ ${reason}`
            : `ออกเอกสารไม่สำเร็จ (${response.status}) ${reason}`,
          "error",
        );
        return;
      }
      const issued = (await response.json()) as { sha256?: string };
      say(
        `ออกเอกสาร ${quote.quote_id} แล้ว · SHA-256 ${(issued.sha256 ?? "").slice(0, 12)}…`,
        "ok",
      );
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : "ออกเอกสารไม่สำเร็จ", "error");
    } finally {
      setBusyId("");
    }
  }

  return (
    <AppShell
      title="ใบเสนอราคา"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say("โหลด LIFF ไม่สำเร็จ", "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      <Count shown={quotes.length} total={quotes.length} />

      {quotes.length === 0 ? (
        <Empty message="ยังไม่มีใบเสนอราคา สร้างได้ในแชทด้วยข้อความ “สร้างใบเสนอราคาจากดีล D-2026-0001”" />
      ) : (
        <ul className="list">
          {quotes.map((quote) => (
            <li key={quote.id} className="card" data-stage={quote.status}>
              <div className="card-title">
                <span className="code">{quote.quote_id}</span>
                <Badge stage={quote.status} label={STATUS_LABELS[quote.status] ?? quote.status} />
              </div>
              <div className="card-meta">
                {quote.generated_document_id ? "ออกเอกสารแล้ว" : "ยังไม่ได้ออกเอกสาร"}
              </div>
              <div className="card-actions">
                <button
                  type="button"
                  className="btn"
                  onClick={() => void preview(quote)}
                  disabled={busyId === quote.id}
                >
                  {busyId === quote.id ? "กำลังทำงาน…" : "ดูตัวอย่าง"}
                </button>
                <button
                  type="button"
                  className="btn"
                  data-variant={quote.generated_document_id ? undefined : "primary"}
                  onClick={() => void issue(quote)}
                  disabled={busyId === quote.id}
                >
                  {quote.generated_document_id ? "ออกเอกสารใหม่" : "ออกเอกสาร"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="footnote">
        ดูตัวอย่างเป็นการสร้าง PDF เพื่อตรวจทานเท่านั้น ไม่บันทึกอะไร ·
        ออกเอกสารจะเก็บไฟล์ถาวรพร้อมบันทึก SHA-256 ว่าลูกค้าได้รับไฟล์ฉบับใด
      </p>
    </AppShell>
  );
}
