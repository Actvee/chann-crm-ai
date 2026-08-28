"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell, Badge, CompanyPicker, Count, Empty } from "../_components";
import { Membership, initLiffSession, proxyHeaders } from "../_lib";

type Deal = {
  id: string;
  deal_id: string;
  stage: string;
  notes?: string | null;
  products?: unknown[];
};

const STAGE_LABELS: Record<string, string> = {
  new: "ใหม่",
  proposed: "เสนอราคาแล้ว",
  won: "สำเร็จ",
  lost: "ไม่สำเร็จ",
};

// Only the moves the Phase 9 state machine actually permits. Offering "won"
// on an already-won deal would put a button in front of someone whose only
// possible outcome is an error.
const NEXT_STAGES: Record<string, string[]> = {
  new: ["proposed", "lost"],
  proposed: ["won", "lost"],
  won: [],
  lost: [],
};

export default function DealList({ liffId }: { liffId: string }) {
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [deals, setDeals] = useState<Deal[]>([]);
  const [status, setStatus] = useState("กำลังเปิด…");
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");
  const [openOnly, setOpenOnly] = useState(false);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/deals`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? "คุณไม่มีสิทธิ์ดูดีล"
          : `โหลดดีลไม่สำเร็จ (${response.status})`,
      );
    }
    setDeals((await response.json()) as Deal[]);
    say("");
  }, [licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : "โหลดดีลไม่สำเร็จ", "error"),
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

  async function setStage(deal: Deal, stage: string) {
    setBusyId(deal.id);
    say(`กำลังเปลี่ยนสถานะ ${deal.deal_id}…`);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/deals/${deal.id}/stage`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({ stage, allow_reopen: false }),
        },
      );
      if (!response.ok) {
        say(
          response.status === 403
            ? "คุณไม่มีสิทธิ์เปลี่ยนสถานะดีล"
            : `เปลี่ยนสถานะไม่สำเร็จ (${response.status})`,
          "error",
        );
        return;
      }
      say(`${deal.deal_id} เปลี่ยนเป็น ${STAGE_LABELS[stage] ?? stage} แล้ว`, "ok");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : "เปลี่ยนสถานะไม่สำเร็จ", "error");
    } finally {
      setBusyId("");
    }
  }

  const visible = openOnly
    ? // Filtering on the two terminal stages rather than listing the open
      // ones means a stage added later counts as open by default, which is
      // the safer direction to be wrong in for a work queue.
      deals.filter((deal) => !["won", "lost"].includes(deal.stage))
    : deals;

  return (
    <AppShell
      title="ดีล"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say("โหลด LIFF ไม่สำเร็จ", "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <button
          type="button"
          className="btn"
          data-variant={openOnly ? undefined : "primary"}
          onClick={() => setOpenOnly(false)}
        >
          ทั้งหมด
        </button>
        <button
          type="button"
          className="btn"
          data-variant={openOnly ? "primary" : undefined}
          onClick={() => setOpenOnly(true)}
        >
          ยังไม่ปิด
        </button>
      </div>

      <Count shown={visible.length} total={deals.length} />

      {visible.length === 0 ? (
        <Empty
          message={
            deals.length === 0
              ? "ยังไม่มีดีล สร้างได้ในแชทด้วยข้อความ “สร้างดีล”"
              : "ไม่มีดีลที่ยังไม่ปิด"
          }
        />
      ) : (
        <ul className="list">
          {visible.map((deal) => (
            <li key={deal.id} className="card" data-stage={deal.stage}>
              <div className="card-title">
                <span className="code">{deal.deal_id}</span>
                <Badge stage={deal.stage} label={STAGE_LABELS[deal.stage] ?? deal.stage} />
              </div>
              <div className="card-meta">
                {(deal.products?.length ?? 0) > 0
                  ? `${deal.products?.length} รายการสินค้า`
                  : "ยังไม่มีรายการสินค้า"}
                {deal.notes ? ` · ${deal.notes}` : ""}
              </div>
              {NEXT_STAGES[deal.stage]?.length ? (
                <div className="card-actions">
                  {NEXT_STAGES[deal.stage].map((stage) => (
                    <button
                      key={stage}
                      type="button"
                      className="btn"
                      data-variant={stage === "won" ? "primary" : undefined}
                      onClick={() => void setStage(deal, stage)}
                      disabled={busyId === deal.id}
                    >
                      {busyId === deal.id
                        ? "กำลังบันทึก…"
                        : `เปลี่ยนเป็น ${STAGE_LABELS[stage] ?? stage}`}
                    </button>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
