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

export default function CustomerList({ liffId }: { liffId: string }) {
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [status, setStatus] = useState("กำลังเริ่ม LIFF…");
  const [busyId, setBusyId] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/customers`, {
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

  async function promote(row: Row) {
    const id = String(row.id);
    setBusyId(id);
    setStatus("กำลังยืนยันเป็นลูกค้า…");
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers/${id}/promote`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        setStatus(
          response.status === 403
            ? "คุณไม่มีสิทธิ์ยืนยันลูกค้า"
            : `ยืนยันไม่สำเร็จ (${response.status})`,
        );
        return;
      }
      setStatus("ยืนยันเป็นลูกค้าเรียบร้อย");
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "ยืนยันไม่สำเร็จ");
    } finally {
      setBusyId("");
    }
  }

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

  // Filtered in the browser, not by refetching: the tenant-scoped list is
  // already loaded and SMB-scale, so a round trip per keystroke would add
  // latency and load for no benefit.
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? rows.filter((row) =>
        [row.first_name, row.last_name, row.phone, row.email, row.customer_id]
          .map((value) => String(value ?? "").toLowerCase())
          .some((value) => value.includes(needle)),
      )
    : rows;

  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: 24 }}>
      <Script
        src={LIFF_SDK_SRC}
        strategy="afterInteractive"
        onReady={() => void initialize()}
        onError={() => setStatus("LIFF SDK load failed")}
      />
      <h1>ลูกค้า</h1>
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

      <label style={{ display: "block", margin: "12px 0" }}>
        ค้นหา
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="ชื่อ เบอร์โทร หรือรหัสลูกค้า"
          style={{ width: "100%" }}
        />
      </label>

      {visible.length === 0 ? (
        <p style={{ color: "#666" }}>ยังไม่มีลูกค้า — เพิ่มผ่านแชทได้ด้วย &ldquo;สร้างลูกค้า&rdquo;</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 8 }}>
          {visible.map((row, index) => (
            <li
              key={String(row.id ?? index)}
              style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12 }}
            >
              <strong>{`${String(row.customer_id ?? "-")} · ${[row.first_name, row.last_name].filter(Boolean).join(" ") || "-"}`}</strong>
              <div style={{ color: "#666", fontSize: 13 }}>{[STAGE_LABELS[String(row.stage)] ?? String(row.stage ?? ""), row.phone, row.email].filter(Boolean).join(" · ")}</div>
              {String(row.stage) === "lead" && (
                <button
                  type="button"
                  onClick={() => void promote(row)}
                  disabled={busyId === String(row.id)}
                  style={{ marginTop: 8 }}
                >
                  {busyId === String(row.id) ? "กำลังทำงาน…" : "ยืนยันเป็นลูกค้า"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
