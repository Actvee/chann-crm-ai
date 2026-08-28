"use client";

import Script from "next/script";
import { useCallback, useEffect, useState } from "react";

import {
  LIFF_SDK_SRC,
  Membership,
  initLiffSession,
  proxyHeaders,
} from "../_lib";

type Row = Record<string, unknown>;

const STAGE_LABELS: Record<string, string> = {
  lead: "ลูกค้ามุ่งหวัง",
  contact: "ลูกค้า",
  new: "ใหม่",
  proposed: "เสนอราคาแล้ว",
  won: "สำเร็จ",
  lost: "ไม่สำเร็จ",
};

export default function ProductList({ liffId }: { liffId: string }) {
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [status, setStatus] = useState("กำลังเริ่ม LIFF…");

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/products`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? "คุณไม่มีสิทธิ์ดูข้อมูลนี้"
          : `โหลดข้อมูลไม่สำเร็จ (${response.status})`,
      );
    }
    setRows((await response.json()) as Row[]);
    setStatus("");
  }, [licenseId, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      setStatus(error instanceof Error ? error.message : "โหลดข้อมูลไม่สำเร็จ"),
    );
  }, [licenseId, load, token]);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      setToken(session.token);
      setMemberships(session.memberships);
      setLicenseId(session.memberships[0]?.license_id ?? "");
      if (!session.memberships.length) setStatus("ยังไม่พบบริษัทที่ผูกไว้");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "LIFF initialization failed");
    }
  }, [liffId]);

  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: 24 }}>
      <Script
        src={LIFF_SDK_SRC}
        strategy="afterInteractive"
        onReady={() => void initialize()}
        onError={() => setStatus("LIFF SDK load failed")}
      />
      <h1>สินค้า</h1>
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

      {rows.length === 0 ? (
        <p style={{ color: "#666" }}>ยังไม่มีสินค้า — เพิ่มผ่านแชทได้ด้วย &ldquo;สร้างสินค้า&rdquo;</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 8 }}>
          {rows.map((row, index) => (
            <li
              key={String(row.id ?? index)}
              style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12 }}
            >
              <strong>{`${String(row.sku ?? "-")} · ${String(row.name ?? "-")}`}</strong>
              <div style={{ color: "#666", fontSize: 13 }}>{row.unit_price != null ? `${Number(row.unit_price).toLocaleString("th-TH", { minimumFractionDigits: 2 })} บาท` : "ยังไม่ได้ตั้งราคา"}</div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
