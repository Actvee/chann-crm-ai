"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../sales/_components";
import { FieldRow } from "../_field-row";
import { shortDate } from "../_list-controls";
import { ProfileCard } from "../_profile-card";
import { ShopSwitcher } from "../_shop-switcher";
import { Ticket, TicketRow } from "../_tickets";
import { Membership, initLiffSession, proxyHeaders } from "../_shared";

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

/** Storefront row — product info and the shop's name, nothing else (9.4). */
type StoreProduct = {
  product_id: string;
  product_name: string;
  sku?: string | null;
  unit_price?: string | number | null;
  license_id: string;
  company_name: string;
};

type ChatSessionView = { id: string; status: string };
type ChatLine = { id: string; sender_type: string; content: string; created_at: string };

type Order = {
  id: string;
  deal_id: string;
  stage: string;
  created_at?: string | null;
  products?: { id: string; product_name: string; qty?: number | null }[];
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
  const [shops, setShops] = useState<Membership[]>([]);
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

  // Spec page 1: the storefront lives on the customer's home too — the
  // same cross-tenant search the chat's "ค้นหา …" makes, and "สนใจ" is
  // the same lead-plus-notification the chat's numbered pick makes.
  const [shopQuery, setShopQuery] = useState("");
  const [shopResults, setShopResults] = useState<StoreProduct[]>([]);
  const [shopSearched, setShopSearched] = useState(false);
  const [orders, setOrders] = useState<Order[]>([]);

  // Phase 15: the customer's conversation with this shop — the same
  // thread the chat's "คุยกับร้าน" runs; the shop answers from its
  // dashboard and the answer lands here and in LINE.
  const [chatSession, setChatSession] = useState<ChatSessionView | null>(null);
  const [chatLines, setChatLines] = useState<ChatLine[]>([]);
  const [chatDraft, setChatDraft] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const headers = proxyHeaders(currentToken, license, "customer");
      const [ticketsRes, warrantiesRes, surveyRes, ordersRes] = await Promise.all([
        fetch(`/api/phase2/licenses/${license}/tickets`, { headers }),
        fetch(`/api/phase2/licenses/${license}/warranties/mine`, { headers }),
        fetch(`/api/phase2/licenses/${license}/surveys/pending`, { headers }),
        fetch(`/api/phase2/licenses/${license}/deals/mine`, { headers }),
      ]);
      // A failed load must not read as "you have no repairs": the empty
      // state and the error state are different facts, and a customer
      // who sees the first when the second is true stops trusting the
      // page. Same rule the technician home applies.
      const failed = [ticketsRes, warrantiesRes, surveyRes, ordersRes].find((res) => !res.ok);
      if (failed) {
        throw new Error(
          failed.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${failed.status})`,
        );
      }
      setTickets((await ticketsRes.json()) as Ticket[]);
      setWarranties((await warrantiesRes.json()) as Warranty[]);
      setOrders((await ordersRes.json()) as Order[]);
      const pending = (await surveyRes.json()) as {
        survey: Survey | null;
        ticket: Ticket | null;
      };
      setSurvey(pending.survey);
      setSurveyTicket(pending.ticket);
    },
    [token, licenseId, t],
  );

  const loadChat = useCallback(
    async (currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const headers = proxyHeaders(currentToken, license, "customer");
      const response = await fetch(
        `/api/phase2/licenses/${license}/chat-sessions?status_filter=live`,
        { headers },
      );
      if (!response.ok) return;
      const rows = (await response.json()) as ChatSessionView[];
      const live = rows[0] ?? null;
      setChatSession(live);
      if (!live) {
        setChatLines([]);
        return;
      }
      const thread = await fetch(
        `/api/phase2/licenses/${license}/chat-sessions/${live.id}/messages`,
        { headers },
      );
      if (thread.ok) {
        const body = (await thread.json()) as { messages: ChatLine[] };
        setChatLines(body.messages);
      }
    },
    [token, licenseId],
  );

  useEffect(() => {
    if (!token || !licenseId || !chatSession) return;
    const timer = setInterval(() => {
      void loadChat().catch(() => undefined);
    }, 10000);
    return () => clearInterval(timer);
  }, [token, licenseId, chatSession, loadChat]);

  async function sendChat() {
    const text = chatDraft.trim();
    if (!chatSession && !text) return;
    setBusy(true);
    try {
      const headers = {
        ...proxyHeaders(token, licenseId, "customer"),
        "Content-Type": "application/json",
      };
      const response = chatSession
        ? await fetch(
            `/api/phase2/licenses/${licenseId}/chat-sessions/${chatSession.id}/messages`,
            { method: "POST", headers, body: JSON.stringify({ content: text }) },
          )
        : await fetch(`/api/phase2/licenses/${licenseId}/chat-sessions`, {
            method: "POST",
            headers,
            body: JSON.stringify({ content: text || null }),
          });
      if (!response.ok) throw new Error(String(response.status));
      setChatDraft("");
      await loadChat();
      say(t.dashboard.customer.chatWaiting, "ok");
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function endChat() {
    if (!chatSession) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/chat-sessions/${chatSession.id}/close`,
        { method: "POST", headers: proxyHeaders(token, licenseId, "customer") },
      );
      if (!response.ok) throw new Error(String(response.status));
      await loadChat();
      say(t.dashboard.customer.chatEnded, "ok");
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

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
      setShops(session.memberships);
      await load(session.token, license);
      await loadChat(session.token, license).catch(() => undefined);
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

  /**
   * Registration is a CLAIM (owner rule, 3 Sep): the shop records the
   * unit it sold; the customer types the sticker and is matched to that
   * row. A serial the shop never recorded is refused with a reason, not
   * turned into a record — same call chat's "ลงทะเบียนสินค้า" makes.
   */
  async function registerWarranty() {
    if (!serial.trim()) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/warranties/claim`,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "customer"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ serial_number: serial.trim() }),
        },
      );
      if (response.status === 404) {
        say(t.dashboard.customer.claimNotFound, "error");
        return;
      }
      if (response.status === 409) {
        say(t.dashboard.customer.claimTaken, "error");
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      setSerial("");
      say(t.dashboard.customer.warrantyRegistered, "ok");
      await load();
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function searchShop(all: boolean) {
    const q = all ? "" : shopQuery.trim();
    if (!all && !q) return;
    setBusy(true);
    try {
      const url = q
        ? `/api/phase2/storefront/products?q=${encodeURIComponent(q)}`
        : "/api/phase2/storefront/products";
      const response = await fetch(url, { headers: proxyHeaders(token, licenseId, "customer") });
      if (!response.ok) throw new Error(String(response.status));
      setShopResults((await response.json()) as StoreProduct[]);
      setShopSearched(true);
      say("", undefined);
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function expressInterest(product: StoreProduct) {
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/storefront/interest`, {
        method: "POST",
        headers: {
          ...proxyHeaders(token, licenseId, "customer"),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          license_id: product.license_id,
          product_name: product.product_name,
          company_name: product.company_name,
        }),
      });
      if (!response.ok) throw new Error(String(response.status));
      say(
        t.dashboard.customer.shopInterestSent.replace("{shop}", product.company_name),
        "ok",
      );
    } catch {
      say(t.dashboard.customer.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function switchShop(licenseIdNext: string) {
    const next = shops.find((s) => s.license_id === licenseIdNext);
    if (!next) return;
    setLicenseId(next.license_id);
    setShopName(next.company_name);
    say(t.dashboard.customer.shopSwitched, "ok");
    await load(token, next.license_id);
    await loadChat(token, next.license_id).catch(() => undefined);
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
        {shops.length > 1 && (
          <ShopSwitcher
            token={token}
            audience="customer"
            shops={shops}
            current={licenseId}
            label={t.dashboard.customer.shopSwitch}
            onSwitched={(id) => void switchShop(id)}
          />
        )}

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
            <h2>{t.dashboard.customer.chatTitle}</h2>
            {chatSession && (
              <span className="card-meta">
                {chatSession.status === "assigned"
                  ? t.dashboard.customer.chatStatusAssigned
                  : t.dashboard.customer.chatStatusOpen}
              </span>
            )}
          </div>
          <p className="card-meta">{t.dashboard.customer.chatHint}</p>
          {chatLines.length > 0 && (
            <ul className="list chat-thread">
              {chatLines.map((line) => (
                <li key={line.id} className="card chat-line" data-sender={line.sender_type}>
                  <div className="card-meta">
                    {line.sender_type === "customer"
                      ? t.dashboard.customer.chatYou
                      : t.dashboard.customer.chatShop}
                  </div>
                  <div>{line.content}</div>
                </li>
              ))}
            </ul>
          )}
          <dl className="fields">
            <FieldRow label={t.dashboard.customer.chatTitle}>
              {(id) => (
                <textarea
                  id={id}
                  rows={2}
                  value={chatDraft}
                  placeholder={t.dashboard.customer.chatPlaceholder}
                  onChange={(e) => setChatDraft(e.target.value)}
                />
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={busy || (!chatSession && !chatDraft.trim()) || (!!chatSession && !chatDraft.trim())}
                onClick={() => void sendChat()}
              >
                {chatSession ? t.dashboard.customer.chatSend : t.dashboard.customer.chatStart}
              </button>
              {chatSession && (
                <button type="button" className="btn" disabled={busy} onClick={() => void endChat()}>
                  {t.dashboard.customer.chatEnd}
                </button>
              )}
            </div>
          </dl>
        </section>

        <section className="section">
          <div className="section-head">
            <h2>{t.dashboard.customer.shopSearch}</h2>
          </div>
          <p className="card-meta">{t.dashboard.customer.shopSearchHint}</p>
          <dl className="fields">
            <FieldRow label={t.dashboard.customer.shopSearch}>
              {(id) => (
                <input
                  id={id}
                  value={shopQuery}
                  placeholder={t.dashboard.customer.shopSearchPlaceholder}
                  onChange={(e) => setShopQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void searchShop(false);
                  }}
                />
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={busy || !shopQuery.trim()}
                onClick={() => void searchShop(false)}
              >
                {t.dashboard.customer.shopSearchButton}
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void searchShop(true)}
              >
                {t.dashboard.customer.shopBrowseAll}
              </button>
            </div>
          </dl>
          {shopSearched && shopResults.length === 0 && (
            <div className="empty">
              <p>{t.dashboard.customer.shopNoResults}</p>
            </div>
          )}
          {shopResults.length > 0 && (
            <ul className="list">
              {shopResults.map((product) => (
                <li key={`${product.license_id}-${product.product_id}`} className="card">
                  <div className="card-title">{product.product_name}</div>
                  <div className="card-meta">
                    {t.dashboard.customer.shopFrom} {product.company_name}
                    {product.unit_price != null && product.unit_price !== ""
                      ? ` · ${Number(product.unit_price).toLocaleString()}`
                      : ""}
                  </div>
                  <div className="card-actions">
                    <button
                      type="button"
                      className="btn"
                      data-variant="primary"
                      disabled={busy}
                      onClick={() => void expressInterest(product)}
                    >
                      {t.dashboard.customer.shopInterested}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="section">
          <div className="section-head">
            <h2>{t.dashboard.customer.reportFault}</h2>
            <a className="btn" data-variant="quiet" href="/liff/customer/guide">
              {t.dashboard.guide.title}
            </a>
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
            <h2>{t.dashboard.customer.registerProduct}</h2>
          </div>
          <p className="card-meta">{t.dashboard.customer.claimHint}</p>
          <dl className="fields">
            <FieldRow label={t.dashboard.customer.serialNumber}>
              {(id) => (
                <input
                  id={id}
                  value={serial}
                  autoCapitalize="characters"
                  onChange={(e) => setSerial(e.target.value)}
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

        <section className="section">
          <div className="section-head">
            <h2>
              {t.dashboard.customer.orders} ({orders.length})
            </h2>
          </div>
          {orders.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.customer.noOrders}</p>
            </div>
          ) : (
            <ul className="list">
              {orders.map((order) => (
                <li key={order.id} className="card">
                  <div className="card-title">
                    {order.deal_id} ·{" "}
                    {(t.dashboard.customer.orderStage as Record<string, string>)[order.stage] ??
                      order.stage}
                  </div>
                  <div className="card-meta">
                    {(order.products ?? [])
                      .map((p) => `${p.product_name}${(p.qty ?? 1) > 1 ? ` ×${p.qty}` : ""}`)
                      .join(", ")}
                    {order.created_at ? ` · ${shortDate(order.created_at)}` : ""}
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
