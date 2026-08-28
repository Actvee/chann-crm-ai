"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell, Badge, CompanyPicker, Count, Empty } from "../_components";
import { Membership, initLiffSession, proxyHeaders } from "../_lib";

type Customer = {
  id: string;
  customer_id: string;
  first_name?: string | null;
  last_name?: string | null;
  stage: string;
  phone?: string | null;
  email?: string | null;
};

const STAGE_LABELS: Record<string, string> = {
  lead: "ลูกค้ามุ่งหวัง",
  contact: "ลูกค้า",
};

function fullName(customer: Customer): string {
  return [customer.first_name, customer.last_name].filter(Boolean).join(" ") || "—";
}

export default function CustomerList({ liffId }: { liffId: string }) {
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [status, setStatus] = useState("กำลังเปิด…");
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");
  const [query, setQuery] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/customers`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? "คุณไม่มีสิทธิ์ดูลูกค้า"
          : `โหลดรายชื่อลูกค้าไม่สำเร็จ (${response.status})`,
      );
    }
    setCustomers((await response.json()) as Customer[]);
    say("");
  }, [licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : "โหลดรายชื่อลูกค้าไม่สำเร็จ", "error"),
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

  async function promote(customer: Customer) {
    setBusyId(customer.id);
    say(`กำลังยืนยัน ${fullName(customer)} เป็นลูกค้า…`);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers/${customer.id}/promote`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(
          response.status === 403
            ? "คุณไม่มีสิทธิ์ยืนยันลูกค้า"
            : `ยืนยันไม่สำเร็จ (${response.status})`,
          "error",
        );
        return;
      }
      say(`${fullName(customer)} เป็นลูกค้าแล้ว`, "ok");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : "ยืนยันไม่สำเร็จ", "error");
    } finally {
      setBusyId("");
    }
  }

  // Filtered in the browser rather than by refetching: the tenant-scoped
  // list is already loaded and SMB-scale, so a round trip per keystroke
  // would add latency for no benefit.
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? customers.filter((customer) =>
        [fullName(customer), customer.phone, customer.email, customer.customer_id]
          .map((value) => String(value ?? "").toLowerCase())
          .some((value) => value.includes(needle)),
      )
    : customers;

  return (
    <AppShell
      title="ลูกค้า"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say("โหลด LIFF ไม่สำเร็จ", "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      <label className="field">
        <span>ค้นหา</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="ชื่อ เบอร์โทร หรือรหัสลูกค้า"
          type="search"
        />
      </label>

      <Count shown={visible.length} total={customers.length} />

      {visible.length === 0 ? (
        <Empty
          message={
            customers.length === 0
              ? "ยังไม่มีลูกค้า เพิ่มได้ในแชทด้วยข้อความ “สร้างลูกค้า”"
              : `ไม่พบลูกค้าที่ตรงกับ “${query}”`
          }
        />
      ) : (
        <ul className="list">
          {visible.map((customer) => (
            <li key={customer.id} className="card" data-stage={customer.stage}>
              <div className="card-title">
                {fullName(customer)}
                <Badge
                  stage={customer.stage}
                  label={STAGE_LABELS[customer.stage] ?? customer.stage}
                />
              </div>
              <div className="card-meta">
                <span className="code">{customer.customer_id}</span>
                {customer.phone ? ` · ${customer.phone}` : ""}
                {customer.email ? ` · ${customer.email}` : ""}
              </div>
              {customer.stage === "lead" && (
                <div className="card-actions">
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => void promote(customer)}
                    disabled={busyId === customer.id}
                  >
                    {busyId === customer.id ? "กำลังบันทึก…" : "ยืนยันเป็นลูกค้า"}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
