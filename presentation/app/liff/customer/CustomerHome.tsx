"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../sales/_components";
import { FieldRow } from "../_field-row";
import { shortDate } from "../_list-controls";
import { ProfileCard } from "../_profile-card";
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

type Survey = {
  id: string;
  ticket_id: string;
  scale_config_json?: Record<string, string> | null;
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
  const [shopName, setShopName] = useState("");
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [warranties, setWarranties] = useState<Warranty[]>([]);
  // Phase 14-C: the survey card, the home-screen twin of the quick reply
  // chat pushes after the last approval.
  const [survey, setSurvey] = useState<Survey | null>(null);
  const [surveyTicket, setSurveyTicket] = useState<Ticket | null>(null);
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
      const [ticketsRes, warrantiesRes, surveyRes] = await Promise.all([
        fetch(`/api/phase2/licenses/${license}/tickets`, { headers }),
        fetch(`/api/phase2/licenses/${license}/warranties/mine`, { headers }),
        fetch(`/api/phase2/licenses/${license}/surveys/pending`, { headers }),
      ]);
      // A failed load must not read as "you have no repairs": the empty
      // state and the error state are different facts, and a customer
      // who sees the first when the second is true stops trusting the
      // page. Same rule the technician home applies.
      const failed = [ticketsRes, warrantiesRes, surveyRes].find((res) => !res.ok);
      if (failed) {
        throw new Error(
          failed.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${failed.status})`,
        );
      }
      setTickets((await ticketsRes.json()) as Ticket[]);
      setWarranties((await warrantiesRes.json()) as Warranty[]);
      const pending = (await surveyRes.json()) as {
        survey: Survey | null;
        ticket: Ticket | null;
      };
      setSurvey(pending.survey);
      setSurveyTicket(pending.ticket);
    },
    [token, licenseId, t],
  );

  async function answerSurvey(score: string) {
    if (!survey) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/surveys/${survey.id}/answer`,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "customer"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ score: Number(score) }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      setSurvey(null);
      setSurveyTicket(null);
      say(t.dashboard.customer.surveyThanks, "ok");
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

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
      setShopName(session.memberships[0]?.company_name ?? "");
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
        {survey && (
          // First on the page: it exists only while an answer is owed,
          // and it is the one thing the shop asked of the customer.
          <section className="section callout" data-tone="ok">
            <div className="section-head">
              <h2>{t.dashboard.customer.surveyTitle}</h2>
            </div>
            <p className="card-meta">
              {t.dashboard.customer.surveyIntro.replace(
                "{code}", surveyTicket?.ticket_number ?? "",
              )}
            </p>
            <div className="card-actions">
              {Object.entries(survey.scale_config_json ?? { "1": "ไม่ดี", "2": "พอใช้", "3": "ดีเยี่ยม" })
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([score, label]) => (
                  <button
                    key={score}
                    type="button"
                    className="btn"
                    data-variant={score === "3" ? "primary" : undefined}
                    disabled={busy}
                    onClick={() => void answerSurvey(score)}
                  >
                    {score} · {label}
                  </button>
                ))}
            </div>
          </section>
        )}

        <section className="section">
          <div className="section-head">
            <h2>{t.dashboard.customer.reportFault}</h2>
          </div>
          {warranties.length === 0 ? (
            // Owner rule (3 Sep): register the product first, so the
            // shop knows which machine the fault is about. Same gate the
            // chat applies; the form appears once one product exists.
            <div className="empty">
              <p>{t.dashboard.customer.registerFirst}</p>
            </div>
          ) : (
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
              <FieldRow label={t.dashboard.customer.whichProduct}>
                {(id) => (
                  <select
                    id={id}
                    value={issueSerial}
                    onChange={(e) => setIssueSerial(e.target.value)}
                  >
                    {warranties.map((row) => (
                      <option key={row.id} value={row.serial_number ?? ""}>
                        {row.product_name ? `${row.product_name} · ` : ""}
                        S/N {row.serial_number}
                      </option>
                    ))}
                    <option value="">{t.dashboard.customer.noSerialOption}</option>
                  </select>
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
          )}
        </section>

        <section className="section">
          <div className="section-head">
            <h2>
              {t.dashboard.customer.repairStatus} ({tickets.length})
            </h2>
          </div>
          {tickets.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.customer.noRepairs}</p>
            </div>
          ) : (
            <ul className="list">
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
        </section>

        <section className="section">
          <div className="section-head">
            <h2>
              {t.dashboard.customer.products} ({warranties.length})
            </h2>
          </div>
          {warranties.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.customer.noProducts}</p>
            </div>
          ) : (
            <ul className="list">
              {warranties.map((row) => (
                <li key={row.id} className="card">
                  <div className="card-title">
                    {row.product_name || row.serial_number}
                  </div>
                  <div className="card-meta">
                    S/N {row.serial_number}
                    {row.warranty_end
                      ? ` · ${t.dashboard.customer.expires} ${shortDate(row.warranty_end)}`
                      : ""}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {token && (
          <ProfileCard token={token} audience="customer" shopName={shopName} />
        )}
      </AppShell>
    </div>
  );
}
