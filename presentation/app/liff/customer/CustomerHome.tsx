"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../sales/_components";
import { FieldRow } from "../_field-row";
import { Ticket, TicketRow } from "../_tickets";
import { initLiffSession, proxyHeaders } from "../_shared";

type Warranty = {
  id: string;
  warranty_number?: string | null;
  serial_number?: string | null;
  product_name?: string | null;
  warranty_start?: string | null;
  warranty_end?: string | null;
  status?: string | null;
};

/**
 * The customer's home — only what a customer does, nothing the shop does.
 *
 * Owner requirement (2 Sep): แจ้งซ่อม, ดูสถานะการซ่อม, ลงทะเบียน
 * รับประกัน. Not the sales dashboard with things hidden: a screen built
 * for staff and then fenced off always leaks staff furniture (labels,
 * empty admin sections, the wrong emphasis). This one is built from the
 * customer's three verbs up.
 *
 * The repair form asks for the fault and, optionally, a serial number —
 * not the customer's own name and phone, which the session already
 * proves better than typing would.
 */
export default function CustomerHome({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const statusLabel = (status: string) =>
    (t.dashboard.tickets.status as Record<string, string>)[status] ?? status;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [warranties, setWarranties] = useState<Warranty[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);

  const [issue, setIssue] = useState("");
  const [issueSerial, setIssueSerial] = useState("");
  const [serial, setSerial] = useState("");
  const [productName, setProductName] = useState("");
  const [purchaseDate, setPurchaseDate] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const headers = proxyHeaders(currentToken, license, "customer");
      const [ticketsRes, warrantiesRes] = await Promise.all([
        fetch(`/api/phase2/licenses/${license}/tickets`, { headers }),
        fetch(`/api/phase2/licenses/${license}/warranties/mine`, { headers }),
      ]);
      // A failed load must not read as "you have no repairs": the empty
      // state and the error state are different facts, and a customer
      // who sees the first when the second is true stops trusting the
      // page. Same rule the technician home applies.
      const failed = [ticketsRes, warrantiesRes].find((res) => !res.ok);
      if (failed) {
        throw new Error(
          failed.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${failed.status})`,
        );
      }
      setTickets((await ticketsRes.json()) as Ticket[]);
      setWarranties((await warrantiesRes.json()) as Warranty[]);
    },
    [token, licenseId, t],
  );

  const onReady = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, "customer");
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setToken(session.token);
      setLicenseId(license);
      await load(session.token, license);
      say("", undefined);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  async function reportFault() {
    if (!issue.trim()) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/tickets`, {
        method: "POST",
        headers: {
          ...proxyHeaders(token, licenseId, "customer"),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          issue_description: issue.trim(),
          serial_number: issueSerial.trim() || undefined,
        }),
      });
      if (!response.ok) throw new Error(String(response.status));
      setIssue("");
      setIssueSerial("");
      say(t.dashboard.customer.reported, "ok");
      await load();
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function registerWarranty() {
    if (!serial.trim()) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/warranties`,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "customer"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            serial_number: serial.trim(),
            product_name: productName.trim() || undefined,
            warranty_start: purchaseDate || undefined,
          }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      setSerial("");
      setProductName("");
      setPurchaseDate("");
      say(t.dashboard.customer.warrantyRegistered, "ok");
      await load();
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-theme="customer">
      <AppShell
        title={t.dashboard.customer.home}
        back={null}
        liffId={liffId}
        onReady={onReady}
        onSdkError={() => say(t.dashboard.openFailed, "error")}
        status={status}
        statusTone={tone}
      >
        <section className="section">
          <div className="section-head">
            <h2>{t.dashboard.customer.reportFault}</h2>
          </div>
          <dl className="fields">
            <FieldRow label={t.dashboard.customer.whatIsWrong}>
              {(id) => (
                <textarea
                  id={id}
                  rows={3}
                  value={issue}
                  onChange={(e) => setIssue(e.target.value)}
                  placeholder={t.dashboard.customer.faultPlaceholder}
                />
              )}
            </FieldRow>
            <FieldRow label={t.dashboard.customer.serialOptional}>
              {(id) => (
                <input
                  id={id}
                  value={issueSerial}
                  onChange={(e) => setIssueSerial(e.target.value)}
                />
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={busy || !issue.trim()}
                onClick={() => void reportFault()}
              >
                {busy ? t.dashboard.related.saving : t.dashboard.customer.submitFault}
              </button>
            </div>
          </dl>
        </section>

        <section className="section">
          <div className="section-head">
            <h2>
              {t.dashboard.customer.repairStatus} ({tickets.length})
            </h2>
          </div>
          {tickets.length === 0 ? (
            <div className="empty">{t.dashboard.customer.noRepairs}</div>
          ) : (
            <ul className="cards">
              {tickets.map((ticket) => (
                <li key={ticket.id} className="card">
                  <TicketRow
                    ticket={ticket}
                    statusLabel={statusLabel(ticket.status)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="section">
          <div className="section-head">
            <h2>{t.dashboard.customer.warranty}</h2>
          </div>
          <dl className="fields">
            <FieldRow label={t.dashboard.customer.serialNumber}>
              {(id) => (
                <input
                  id={id}
                  value={serial}
                  onChange={(e) => setSerial(e.target.value)}
                />
              )}
            </FieldRow>
            <FieldRow label={t.dashboard.customer.productName}>
              {(id) => (
                <input
                  id={id}
                  value={productName}
                  onChange={(e) => setProductName(e.target.value)}
                />
              )}
            </FieldRow>
            <FieldRow label={t.dashboard.customer.purchaseDate}>
              {(id) => (
                <input
                  id={id}
                  type="date"
                  value={purchaseDate}
                  onChange={(e) => setPurchaseDate(e.target.value)}
                />
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={busy || !serial.trim()}
                onClick={() => void registerWarranty()}
              >
                {busy
                  ? t.dashboard.related.saving
                  : t.dashboard.customer.registerWarranty}
              </button>
            </div>
          </dl>
          {warranties.length > 0 && (
            <ul className="cards" style={{ marginTop: 12 }}>
              {warranties.map((row) => (
                <li key={row.id} className="card">
                  <div className="card-title">
                    {row.product_name || row.serial_number}
                  </div>
                  <div className="card-meta">
                    S/N {row.serial_number}
                    {row.warranty_end
                      ? ` · ${t.dashboard.customer.expires} ${row.warranty_end}`
                      : ""}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </AppShell>
    </div>
  );
}
