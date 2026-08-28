"use client";

import Script from "next/script";
import { useCallback, useEffect, useState } from "react";

type Membership = { license_id: string; license_code: string; company_name: string };

type Quote = {
  id: string;
  quote_id: string;
  status: string;
  created_at: string;
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
  const [status, setStatus] = useState("กำลังเริ่ม LIFF…");
  const [busyId, setBusyId] = useState("");

  const headers = useCallback(
    () => ({
      "Content-Type": "application/json",
      "X-Liff-ID-Token": token,
      "X-Liff-Audience": "sales",
      "X-License-Id": licenseId,
    }),
    [token, licenseId],
  );

  const loadQuotes = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/quotes`, {
      headers: headers(),
    });
    if (!response.ok) throw new Error(`โหลดใบเสนอราคาไม่สำเร็จ (${response.status})`);
    setQuotes((await response.json()) as Quote[]);
    setStatus("");
  }, [headers, licenseId, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void loadQuotes().catch((error: unknown) =>
      setStatus(error instanceof Error ? error.message : "โหลดใบเสนอราคาไม่สำเร็จ"),
    );
  }, [licenseId, loadQuotes, token]);

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

  async function openPdf(quote: Quote) {
    setBusyId(quote.id);
    setStatus(`กำลังสร้าง PDF ของ ${quote.quote_id}…`);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quote.id}/pdf`,
        { headers: headers() },
      );
      if (!response.ok) {
        // 409 carries the specific reason (which company fields are still
        // blank), which is far more useful than a status code alone.
        let detail = "";
        try {
          detail = ((await response.json()) as { detail?: string }).detail ?? "";
        } catch {
          detail = "";
        }
        if (response.status === 409) {
          setStatus(`ยังออกเอกสารไม่ได้: ${detail} — กรอกข้อมูลบริษัทให้ครบก่อน`);
        } else if (response.status === 503) {
          setStatus("ยังไม่ได้ตั้งค่าตัวสร้าง PDF (SmartBrowz)");
        } else {
          setStatus(`สร้าง PDF ไม่สำเร็จ (${response.status}) ${detail}`);
        }
        return;
      }
      // Opened as a blob rather than navigating to the URL: the request
      // needs LIFF auth headers, which a plain <a href> cannot send.
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
      // Revoked on a delay so the new tab has time to read it; revoking
      // immediately races the browser and shows a blank tab.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      setStatus(`เปิด PDF ของ ${quote.quote_id} แล้ว`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "สร้าง PDF ไม่สำเร็จ");
    } finally {
      setBusyId("");
    }
  }

  async function issue(quote: Quote) {
    if (
      !window.confirm(
        `ออกเอกสารสำหรับ ${quote.quote_id}?\n\n` +
          "เอกสารที่ออกแล้วจะถูกเก็บถาวรและบันทึกเป็นหลักฐานว่าลูกค้าได้รับไฟล์นี้",
      )
    ) {
      return;
    }
    setBusyId(quote.id);
    setStatus(`กำลังออกเอกสาร ${quote.quote_id}…`);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quote.id}/issue`,
        { method: "POST", headers: headers() },
      );
      let detail = "";
      try {
        detail = ((await response.clone().json()) as { detail?: string }).detail ?? "";
      } catch {
        detail = "";
      }
      if (!response.ok) {
        if (response.status === 409) {
          setStatus(`ยังออกเอกสารไม่ได้: ${detail} — กรอกข้อมูลบริษัทให้ครบก่อน`);
        } else if (response.status === 503) {
          setStatus(`ยังไม่พร้อมออกเอกสาร: ${detail}`);
        } else {
          setStatus(`ออกเอกสารไม่สำเร็จ (${response.status}) ${detail}`);
        }
        return;
      }
      const issued = (await response.json()) as { sha256?: string };
      setStatus(
        `ออกเอกสาร ${quote.quote_id} เรียบร้อย — SHA-256: ${(issued.sha256 ?? "").slice(0, 16)}…`,
      );
      await loadQuotes();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "ออกเอกสารไม่สำเร็จ");
    } finally {
      setBusyId("");
    }
  }

  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: 24 }}>
      <Script
        src="https://static.line-scdn.net/liff/edge/versions/2.29.2/sdk.js"
        strategy="afterInteractive"
        onReady={() => void initialize()}
        onError={() => setStatus("LIFF SDK load failed")}
      />
      <h1>ใบเสนอราคา</h1>
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

      {quotes.length === 0 ? (
        <p style={{ color: "#666" }}>
          ยังไม่มีใบเสนอราคา — สร้างผ่านแชทได้ด้วย &ldquo;สร้างใบเสนอราคาจากดีล D-2026-0001&rdquo;
        </p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 8 }}>
          {quotes.map((quote) => (
            <li
              key={quote.id}
              style={{
                border: "1px solid #ddd",
                borderRadius: 6,
                padding: 12,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
              }}
            >
              <div>
                <strong>{quote.quote_id}</strong>
                <div style={{ color: "#666", fontSize: 13 }}>
                  {STATUS_LABELS[quote.status] ?? quote.status}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  onClick={() => void openPdf(quote)}
                  disabled={busyId === quote.id || !licenseId}
                >
                  {busyId === quote.id ? "กำลังทำงาน…" : "ดูตัวอย่าง"}
                </button>
                <button
                  type="button"
                  onClick={() => void issue(quote)}
                  disabled={busyId === quote.id || !licenseId}
                >
                  ออกเอกสาร
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p style={{ fontSize: 13, color: "#888", marginTop: 24 }}>
        &ldquo;ดูตัวอย่าง&rdquo; สร้าง PDF เพื่อตรวจทานเท่านั้น ไม่บันทึกอะไร ·
        &ldquo;ออกเอกสาร&rdquo; จะเก็บไฟล์ถาวรพร้อมบันทึกหลักฐาน SHA-256
        ว่าลูกค้าได้รับไฟล์ฉบับใด
      </p>
    </main>
  );
}
