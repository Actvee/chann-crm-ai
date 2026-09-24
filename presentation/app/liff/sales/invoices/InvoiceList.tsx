"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Count, Empty } from "../_components";
import { RelatedLinks } from "../_record";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { ListFilters, optionsFrom } from "../../_filters";
import { usePagedList } from "../../_paged-list";
import { ListControls, byNewest, byOldest, useListControls } from "../../_list-controls";
import { Sheet } from "../../_sheet";
import { FieldRow } from "../../_field-row";
import { ConfirmDialog, useConfirm } from "../../_confirm";
import { ProductLineForm } from "../../_product-line-form";

import { readFailure, useFailureText, useFormatters } from "../_format";
import { openExternal, proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { CreateInvoiceButton } from "./_create-button";
import { InvoiceCreateSheet, type CreatedInvoice } from "./_create-sheet";

/**
 * Round 20V — invoices, payments and receipts (owner, 21 ก.ย. 2569:
 * "ทำข้อ 2 … รวมเอาเรื่อง invoice").
 *
 * One page: the list, and a sheet for the invoice in hand. The sheet is
 * where the money moves — issue the PDF, record a payment (a deposit,
 * an instalment, the balance), issue the receipt, void — so those
 * buttons sit next to the lines and the ledger they change, never on a
 * row nobody has read yet (the quote list's lesson, 17 ก.ย. 2569).
 *
 * Money is printed in the data font and right-aligned so two rows
 * compare by eye; a status is always a word on a badge, the colour only
 * echoes it (ui-ux-pro-max: never colour alone). The amount field uses
 * inputMode="decimal" so a phone opens the number keyboard, and every
 * field has a visible label.
 *
 * Round 20X (owner, 22 ก.ย. 2569: "invoice ต้องมีตัวเลือกให้ผูกกับ deal หรือ
 * ลูกค้าได้"): the page can now MAKE an invoice — "สร้างใบแจ้งหนี้" opens
 * the form in `_create-sheet.tsx` — and honours `?contact_id=` and
 * `?deal_id=` as filters, so the customer page and the deal page can
 * link to "their" bills. `&create=1` on either opens the form prefilled.
 */

type Payment = {
  id: string;
  amount: string;
  method: string;
  paid_at: string;
  reference?: string | null;
  note?: string | null;
};

type Line = {
  line_no: number;
  product_name: string;
  qty: number;
  unit_price: string;
  line_total: string;
  notes?: string | null;
};

type Invoice = {
  created_at?: string | null;
  id: string;
  invoice_id: string;
  status: string;
  contact_id?: string | null;
  quote_id?: string | null;
  deal_id?: string | null;
  issue_date?: string | null;
  due_date?: string | null;
  total: string;
  paid_amount: string;
  outstanding: string;
  is_overdue: boolean;
  note?: string | null;
  generated_document_id?: string | null;
  receipt_document_id?: string | null;
  // Round 21C — set by the Application tier when the lines have been
  // corrected since the last PDF: the file on record no longer matches
  // this bill.
  needs_reissue?: boolean | null;
  // Round 21C, ruling 18 — read straight from the Application tier's
  // `get_invoice` (computed the same way `document_send.py` decides
  // `CustomerNotLinked`): the sheet's send button reads this field
  // directly, never a client-side lookup of its own.
  customer_has_line?: boolean | null;
  data_snapshot?: {
    customer?: { name?: string };
    quote?: { quote_id?: string };
    deal?: { deal_id?: string };
    line_items?: Line[];
    totals?: { subtotal?: string; discount_amount?: string | null; vat_amount?: string | null; grand_total?: string };
  } | null;
  payments?: Payment[];
};

const METHODS = ["transfer", "cash", "promptpay", "card", "other"] as const;

export default function InvoiceList({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const failureText = useFailureText();
  const { money, shortDate } = useFormatters();
  const statusLabel = (status: string) =>
    (t.invoice.status as Record<string, string>)[status] ?? status;
  const methodLabel = (method: string) =>
    (t.dashboard.invoices.methods as Record<string, string>)[method] ?? method;
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [open, setOpen] = useState<Invoice | null>(null);
  const [paying, setPaying] = useState(false);
  const [form, setForm] = useState({ amount: "", method: "transfer", paid_at: "", reference: "" });
  // Round 21C — correcting a bill before anyone has paid it (the lines),
  // and handing the document to the customer on LINE (the send button).
  const [editingLines, setEditingLines] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [editingLineIndex, setEditingLineIndex] = useState(-1);
  const [addingLine, setAddingLine] = useState(false);
  // Round 20X: the create form and the URL's filters.
  const [creating, setCreating] = useState(false);
  const [contactFilter, setContactFilter] = useState("");
  const [dealFilter, setDealFilter] = useState("");
  const [filterName, setFilterName] = useState("");
  const [prefill, setPrefill] = useState<{ contactId?: string; dealId?: string }>({});
  const [wantedId, setWantedId] = useState("");

  useEffect(() => {
    // Read once, on the client: the page is rendered dynamically and the
    // LIFF redirect can only carry query strings.
    const params = new URLSearchParams(window.location.search);
    const contactId = params.get("contact_id") ?? "";
    const dealId = params.get("deal_id") ?? "";
    // Round 21A: a quote (or a freshly created bill) points straight at
    // one invoice — the page opens it instead of making someone find it.
    const wanted = params.get("invoice_id") ?? "";
    if (wanted) setWantedId(wanted);
    setContactFilter(contactId);
    setDealFilter(dealId);
    setPrefill({ contactId: contactId || undefined, dealId: dealId || undefined });
    if (params.get("create") === "1") setCreating(true);
  }, []);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  const listError = useCallback(
    (_message: string, httpStatus?: number) =>
      say(
        httpStatus === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed}${httpStatus ? ` (${httpStatus})` : ""}`,
        "error",
      ),
    [say, t],
  );
  const list = usePagedList<Invoice>({
    token, licenseId, ready: session.ready,
    path: `licenses/${licenseId}/invoices`,
    params: {
      status_filter: statusFilter, overdue: overdueOnly ? "true" : "",
      contact_id: contactFilter, deal_id: dealFilter,
    },
    onError: listError,
  });
  const invoices = list.rows;
  const load = list.reload;

  // The filter chip names the record, not its id: "เฉพาะลูกค้า สมชาย ใจดี"
  // rather than a UUID nobody can read.
  useEffect(() => {
    if (!session.ready || !token || !licenseId) return;
    let cancelled = false;
    void (async () => {
      try {
        const headers = proxyHeaders(token, licenseId);
        if (dealFilter) {
          const response = await fetch(`/api/phase2/licenses/${licenseId}/deals/${dealFilter}`, { headers });
          if (!cancelled && response.ok) setFilterName(String(((await response.json()) as { deal_id?: string }).deal_id ?? ""));
        } else if (contactFilter) {
          const response = await fetch(`/api/phase2/licenses/${licenseId}/customers/${contactFilter}`, { headers });
          if (!cancelled && response.ok) {
            const row = (await response.json()) as { first_name?: string | null; last_name?: string | null; customer_id?: string };
            setFilterName([row.first_name, row.last_name].filter(Boolean).join(" ") || String(row.customer_id ?? ""));
          }
        } else {
          setFilterName("");
        }
      } catch {
        // The chip falls back to its plain label.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session.ready, token, licenseId, contactFilter, dealFilter]);

  function clearFilters() {
    setContactFilter("");
    setDealFilter("");
    setFilterName("");
    window.history.replaceState(null, "", window.location.pathname);
  }

  /** After the form: the list refreshes and the new bill's own sheet
   *  opens, where the PDF and the payment buttons are. */
  async function created(invoice: CreatedInvoice) {
    setCreating(false);
    await load();
    await openInvoice(invoice as unknown as Invoice);
  }

  useEffect(() => {
    if (session.ready && !list.busy) say("");
  }, [session.ready, list.busy, say]);

  // The invoice a link named (?invoice_id=…): fetched on its own, so it
  // opens even when it is not on the first page of the list.
  useEffect(() => {
    if (!session.ready || !token || !licenseId || !wantedId) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${wantedId}`, {
          headers: proxyHeaders(token, licenseId),
        });
        if (!cancelled && response.ok) {
          const row = (await response.json()) as Invoice;
          setOpen(row);
          setPaying(false);
          setEditingLines(false);
          setLines((row.data_snapshot?.line_items ?? []) as Line[]);
        }
      } catch {
        /* the list is still there to find it in */
      } finally {
        if (!cancelled) setWantedId("");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session.ready, token, licenseId, wantedId]);

  const can = (key: string) => !session.suspended && permissions.has(key);
  const canUpdate = can("invoice.update");
  const canVoid = can("invoice.void");
  const canCreate = can("invoice.create");

  /** The row with its ledger: the list leaves `payments` empty on purpose. */
  async function openInvoice(row: Invoice) {
    setOpen(row);
    setPaying(false);
    setEditingLines(false);
    setForm({ amount: "", method: "transfer", paid_at: "", reference: "" });
    setLines((row.data_snapshot?.line_items ?? []) as Line[]);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${row.id}`, {
        headers: proxyHeaders(token, licenseId),
      });
      if (response.ok) {
        const full = (await response.json()) as Invoice;
        setOpen(full);
        setLines((full.data_snapshot?.line_items ?? []) as Line[]);
      }
    } catch {
      // The row already on screen is a complete answer; the ledger is extra.
    }
  }

  async function refreshOpen(id: string) {
    await load();
    const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${id}`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (response.ok) {
      const full = (await response.json()) as Invoice;
      setOpen(full);
      setLines((full.data_snapshot?.line_items ?? []) as Line[]);
    }
  }

  async function openDocument(documentId: string) {
    say(t.dashboard.working);
    try {
      // A signed https link, never a blob: URL — LINE's browser refuses those.
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/documents/${documentId}/link`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const { url } = (await response.json()) as { url: string };
      openExternal(url);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    }
  }

  async function issue(row: Invoice) {
    const already = Boolean(row.generated_document_id);
    const ok = await ask({
      action: already ? t.dashboard.invoices.reissue : t.dashboard.invoices.issue,
      target: t.dashboard.invoices.invoiceWord,
      code: row.invoice_id,
      affects: [already ? t.dashboard.invoices.reissueAffects : t.dashboard.invoices.issueAffects],
      reversible: t.dashboard.invoices.issueKeeps,
      confirmLabel: already ? t.dashboard.invoices.reissue : t.dashboard.invoices.issue,
    });
    if (!ok) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/issue?allow_reissue=${already}`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const issued = (await response.json()) as { generated_document_id?: string };
      say(`${row.invoice_id} — ${t.dashboard.invoices.issued}`, "ok");
      await refreshOpen(row.id);
      if (issued.generated_document_id) await openDocument(String(issued.generated_document_id));
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  async function savePayment(row: Invoice) {
    setBusy(true);
    say(t.dashboard.working);
    try {
      const amount = form.amount.trim().replace(/,/g, "");
      const body: Record<string, unknown> = {
        method: form.method,
        reference: form.reference.trim() || null,
        // An empty amount means "the whole balance" — said on the field.
        full: amount === "",
      };
      if (amount !== "") body.amount = amount;
      if (form.paid_at) body.paid_at = `${form.paid_at}T12:00:00+07:00`;
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/payments`,
        { method: "POST", headers: proxyHeaders(token, licenseId), body: JSON.stringify(body) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      // A payment that settles the bill closes its deal (round 21C) — said
      // here as chat says it, from the `closed_deal` THIS payment returned,
      // never inferred from status; nothing when it is null (final review
      // I5; ui-ux-pro-max: success-feedback — the change is confirmed, not
      // left for the person to discover on the deal page).
      const saved = (await response.json().catch(() => ({}))) as {
        closed_deal?: { deal_id?: string; amount?: string | number | null } | null;
      };
      const closedLine = saved.closed_deal ?
        ` · ${t.dashboard.invoices.dealClosed
          .replace("{deal}", String(saved.closed_deal.deal_id ?? ""))
          .replace("{amount}", money(saved.closed_deal.amount ?? 0))}`
        : "";
      say(`${row.invoice_id} — ${t.dashboard.invoices.paymentSaved}${closedLine}`, "ok");
      setPaying(false);
      setForm({ amount: "", method: "transfer", paid_at: "", reference: "" });
      await refreshOpen(row.id);
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  /** Replace the whole line list — the Application tier recomputes every
   *  money column and hands back the saved invoice, which is the only
   *  copy of the totals this screen ever displays (round 20K: a total the
   *  browser adds up itself is a second source of truth). A 409 means
   *  money has been recorded since the sheet opened. */
  async function saveLines(next: Line[]) {
    if (!open) return;
    // A bill must keep at least one line — the delete control is already
    // disabled on the last one, and this is the same rule enforced
    // regardless of how `next` was built (review round 1, finding 1).
    if (next.length === 0) {
      say(t.dashboard.invoices.oneLineRequired, "error");
      return;
    }
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${open.id}/lines`,
        {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            lines: next.map((line) => ({
              product_name: line.product_name,
              qty: Number(line.qty ?? 1),
              unit_price: String(line.unit_price ?? "0"),
            })),
          }),
        },
      );
      if (!response.ok) {
        say(response.status === 409 ? t.dashboard.invoices.editLocked : await failureText(response), "error");
        return;
      }
      const saved = (await response.json()) as Invoice;
      // The lines PATCH doesn't answer "does the customer have LINE" —
      // only `get_invoice` does (ruling 18) — and editing lines can't
      // change that, so the previously-known answer carries forward
      // rather than reading as unknown/false until the next full fetch.
      setOpen({ ...saved, customer_has_line: open.customer_has_line });
      setLines((saved.data_snapshot?.line_items ?? []) as Line[]);
      say(t.dashboard.saved, "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  async function saveLine(index: number, line: { name: string; qty: number; price: string }) {
    setEditingLineIndex(-1);
    await saveLines(
      lines.map((existing, i) =>
        i === index
          ? { ...existing, product_name: line.name, qty: line.qty, unit_price: line.price }
          : existing,
      ),
    );
  }

  async function addNewLine(line: { name: string; qty: number; price: string }) {
    setAddingLine(false);
    await saveLines([
      ...lines,
      { line_no: lines.length + 1, product_name: line.name, qty: line.qty, unit_price: line.price, line_total: "0" },
    ]);
  }

  async function removeLine(index: number) {
    // The delete control is disabled on the last line already; this is
    // the same rule enforced again so nothing but a disabled button ever
    // stands between a person and this action (review round 1, finding 1).
    if (lines.length <= 1) {
      say(t.dashboard.invoices.oneLineRequired, "error");
      return;
    }
    const target = lines[index];
    const ok = await ask({
      action: t.common.delete,
      target: target.product_name,
      permanent: true,
      confirmLabel: t.common.delete,
    });
    if (!ok) return;
    await saveLines(lines.filter((_, i) => i !== index));
  }

  /** Hand the document to the customer on LINE — the invoice PDF, or its
   *  receipt once the bill is paid. `resent` from the response is always
   *  false today (round 21C ruling 15), so this never claims a re-send:
   *  the toast is the same sentence every time, and the time itself is
   *  not shown because the server does not give one. */
  async function sendToCustomer(kind: "invoice" | "receipt") {
    if (!open) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${open.id}/send`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ kind }),
      });
      if (!response.ok) {
        // Match on the exact codes `_document_send_error` returns
        // (customer_not_linked / not_issued) — anything else (a 500, a
        // rate limit, …) is a real, different failure and must say so,
        // not be folded into "not issued yet" (review round 1, finding 4).
        const failure = await readFailure(response);
        if (failure.code === "customer_not_linked") {
          say(t.dashboard.invoices.sendNoLine, "error");
        } else if (failure.code === "not_issued") {
          say(t.dashboard.invoices.sendNotIssued, "error");
        } else if (failure.code === "push_failed") {
          // LINE did not take it (round 21E): a failure, never "sent", and
          // said as its cause — LINE's raw answer stays in the server log.
          say(
            failure.reason === "not_configured"
              ? t.dashboard.invoices.sendPushNotConfigured
              : failure.reason === "blocked"
                ? t.dashboard.invoices.sendPushBlocked
                : failure.reason === "channel_refused"
                  ? t.dashboard.invoices.sendPushChannel
                  : t.dashboard.invoices.sendPushFailed,
            "error",
          );
        } else if (failure.code === "void") {
          say(t.dashboard.invoices.sendVoid, "error");
        } else if (failure.code === "needs_reissue") {
          say(t.dashboard.invoices.sendNeedsReissue, "error");
        } else {
          say(await failureText(response), "error");
        }
        return;
      }
      const data = (await response.json()) as { customer_name?: string };
      say(t.dashboard.invoices.sendDone.replace("{name}", data.customer_name ?? ""), "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  /** Why "ส่งให้ลูกค้า" cannot be pressed on this bill, or null when it
   *  can. One reason at a time, in the order the server refuses them
   *  (`document_send.why_not_sendable`, then no PDF, then no LINE): a
   *  void bill or a bill corrected since its PDF was made must never
   *  reach the customer — the link cannot be recalled (final review C1).
   *  The receipt is its own document, so a stale INVOICE PDF does not
   *  hold it back. `customer_has_line` is undefined while the full
   *  invoice loads: unknown, so disabled without claiming a reason. */
  function sendBlocked(row: Invoice): string | null {
    const kind = row.receipt_document_id ? "receipt" : "invoice";
    if (row.status === "void") return t.dashboard.invoices.sendVoid;
    if (kind === "invoice" && row.needs_reissue) return t.dashboard.invoices.sendNeedsReissue;
    if (row.customer_has_line === false) return t.dashboard.invoices.sendNoLine;
    if (!row.generated_document_id) return t.dashboard.invoices.sendNotIssued;
    return null;
  }

  async function issueReceipt(row: Invoice) {
    const already = Boolean(row.receipt_document_id);
    const ok = await ask({
      action: already ? t.dashboard.invoices.reissueReceipt : t.dashboard.invoices.issueReceipt,
      target: t.dashboard.invoices.invoiceWord,
      code: row.invoice_id,
      affects: [t.dashboard.invoices.receiptAffects],
      reversible: t.dashboard.invoices.receiptKeeps,
      confirmLabel: already ? t.dashboard.invoices.reissueReceipt : t.dashboard.invoices.issueReceipt,
    });
    if (!ok) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/receipt?allow_reissue=${already}`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const issued = (await response.json()) as { receipt_document_id?: string };
      say(`${row.invoice_id} — ${t.dashboard.invoices.receiptIssued}`, "ok");
      await refreshOpen(row.id);
      if (issued.receipt_document_id) await openDocument(String(issued.receipt_document_id));
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  async function voidInvoice(row: Invoice) {
    const ok = await ask({
      action: t.dashboard.invoices.voidAction,
      target: t.dashboard.invoices.invoiceWord,
      code: row.invoice_id,
      affects: [t.dashboard.invoices.voidAffects],
      permanent: true,
      confirmLabel: t.dashboard.invoices.voidAction,
    });
    if (!ok) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/void`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      say(`${row.invoice_id} — ${t.dashboard.invoices.voided}`, "ok");
      await refreshOpen(row.id);
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  const sorts = [
    { key: "newest", label: t.dashboard.list.newest, compare: byNewest<Invoice> },
    { key: "oldest", label: t.dashboard.list.oldest, compare: byOldest<Invoice> },
    {
      key: "code",
      label: t.dashboard.list.byCode,
      compare: (a: Invoice, b: Invoice) => b.invoice_id.localeCompare(a.invoice_id),
    },
  ];
  // Status, search and overdue are applied by the database (round 20N).
  const controls = useListControls(invoices, sorts, "newest");
  const visible = controls.visible;
  const customerName = (row: Invoice) => row.data_snapshot?.customer?.name || "—";
  const isOpen = (row: Invoice) => row.status === "issued" || row.status === "partially_paid";
  /** The one thing to press next on this bill, or null. */
  const sheetPrimary = (row: Invoice): "issue" | "pay" | "receipt" | null => {
    if (row.status !== "void" && row.status !== "paid" && !row.generated_document_id) return "issue";
    if (isOpen(row) && !paying) return "pay";
    if (row.status === "paid" && !row.receipt_document_id) return "receipt";
    return null;
  };
  // Round 21C: `lines` is now state (set whenever the sheet opens or the
  // invoice is re-read), so a correction can be edited in place without
  // waiting on the invoice object it will eventually replace.
  const totals = open?.data_snapshot?.totals;
  // Editable only until money has touched the bill — the invoice's answer
  // to the quote's "draft only" rule, and the same test `invoices.py`'s
  // `edit_lines` makes server-side (a void invoice's paid_amount is also
  // zero, so `status !== "void"` is this screen's own extra guard).
  const editable =
    canUpdate && Number(open?.paid_amount ?? 0) === 0 && open?.status !== "void";

  return (
    <SalesShell
      session={session}
      title={t.dashboard.invoices.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <ListFilters
        query={list.query}
        onQuery={list.setQuery}
        status={statusFilter}
        statuses={optionsFrom(t.invoice.status as Record<string, string>)}
        onStatus={setStatusFilter}
      >
        <ListControls
          sorts={sorts}
          sortKey={controls.sortKey}
          onSort={controls.setSortKey}
          from={controls.from}
          to={controls.to}
          onFrom={controls.setFrom}
          onTo={controls.setTo}
        />
        <label className="filter-check">
          <input
            type="checkbox"
            checked={overdueOnly}
            onChange={(event) => setOverdueOnly(event.target.checked)}
          />{" "}
          {t.dashboard.invoices.overdueOnly}
        </label>
      </ListFilters>

      {/* Round 21E (owner, 24 ก.ย. 2569: "ปุ่มสร้างใบแจ้งหนี้ก็ไม่เหมือนใน
          ใบเสนอราคา"). The quote page's button, the same component: the same
          label, a primary full width on a phone, in its own row above the
          list where the customer and deal lists put their create button.
          It used to be a compact "สร้างใบแจ้งหนี้" in the count line. */}
      {canCreate && (
        <div className="actions record-create">
          <CreateInvoiceButton onClick={() => setCreating(true)} />
        </div>
      )}

      <div className="list-head">
        {/* A list narrowed by a URL the person cannot see is a puzzle
            (the filter bar's own rule): the narrowing is named, by the
            record's name or code, with the way out beside it. */}
        {contactFilter || dealFilter ? (
          <p className="count">
            {dealFilter
              ? t.dashboard.invoices.filteredByDeal.replace("{code}", filterName || dealFilter)
              : t.dashboard.invoices.filteredByCustomer.replace("{name}", filterName || contactFilter)}
          </p>
        ) : (
          <Count shown={visible.length} total={list.total ?? invoices.length} />
        )}
        {(contactFilter || dealFilter) && (
          <div className="list-tools">
            {(contactFilter || dealFilter) && (
              <button type="button" className="btn" data-variant="quiet" onClick={clearFilters}>
                {t.dashboard.invoices.clearFilter}
              </button>
            )}
          </div>
        )}
      </div>

      {visible.length === 0 ? (
        <Empty
          message={
            list.searching
              ? t.dashboard.opening
              : list.query || statusFilter || overdueOnly
                ? t.dashboard.noMatch
                : t.dashboard.invoices.empty
          }
        />
      ) : (
        <ul className="list">
          {visible.map((row) => (
            <li
              key={row.id}
              className="card"
              data-stage={row.status}
              data-overdue={row.is_overdue ? "true" : undefined}
            >
              <button type="button" className="row-link" onClick={() => void openInvoice(row)}>
                <span className="row-body">
                  <span className="card-title">
                    <span className="code">{row.invoice_id}</span>
                    <Badge stage={row.status} label={statusLabel(row.status)} />
                    {row.is_overdue && (
                      <Badge stage="overdue" label={t.dashboard.invoices.overdue} />
                    )}
                  </span>
                  <span className="card-meta">
                    {customerName(row)}
                    {" · "}
                    {t.dashboard.invoices.total} <span className="money-line">{money(row.total)}</span>
                    {isOpen(row) && (
                      <>
                        {" · "}
                        {t.dashboard.invoices.outstanding}{" "}
                        <span className="money-line">{money(row.outstanding)}</span>
                      </>
                    )}
                    {row.due_date && isOpen(row) && (
                      <>
                        {" · "}
                        {t.dashboard.invoices.due} {shortDate(row.due_date)}
                      </>
                    )}
                    {!row.generated_document_id && row.status === "draft" && (
                      <>
                        {" · "}
                        {t.dashboard.invoices.notIssued}
                      </>
                    )}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {list.hasMore && (
        <div className="actions">
          <button type="button" className="btn" disabled={list.busy} onClick={list.loadMore}>
            {list.busy ? t.dashboard.opening : t.dashboard.list.loadMore}
          </button>
        </div>
      )}

      {!canUpdate && !session.suspended && (
        <p className="footnote">{t.dashboard.invoices.needsUpdate}</p>
      )}
      <p className="footnote">{t.dashboard.invoices.note}</p>

      <Sheet open={Boolean(open)} title={open?.invoice_id ?? ""} onClose={() => setOpen(null)}>
        {open && (
          <>
            <p className="card-meta">
              <Badge stage={open.status} label={statusLabel(open.status)} />
              {open.is_overdue && <Badge stage="overdue" label={t.dashboard.invoices.overdue} />}
              {" "}
              {customerName(open)}
            </p>

            {/* Owner, 22 ก.ย. 2569: "จากใบแจ้งหนี้ก็ควรกดไปที่ record ที่
                เกี่ยวข้องได้ด้วย" — the codes were printed as text, so the
                way back to the deal was the search box. */}
            <RelatedLinks
              items={[
                ...(open.contact_id
                  ? [{ href: `/liff/sales/customers/${open.contact_id}`, label: t.customer.title, code: customerName(open) }]
                  : []),
                ...(open.deal_id
                  ? [{ href: `/liff/sales/deals/${open.deal_id}`, label: t.deal.title, code: open.data_snapshot?.deal?.deal_id ?? "" }]
                  : []),
                ...(open.quote_id
                  ? [{ href: `/liff/sales/quotes/${open.quote_id}`, label: t.quote.title, code: open.data_snapshot?.quote?.quote_id ?? "" }]
                  : []),
              ]}
            />

            <dl className="fields">
              <FieldRow label={t.dashboard.invoices.total}>
                <span className="money-line">{money(open.total)}</span>
              </FieldRow>
              <FieldRow label={t.dashboard.invoices.paid}>
                <span className="money-line">{money(open.paid_amount)}</span>
              </FieldRow>
              <FieldRow label={t.dashboard.invoices.outstanding}>
                <span className="money-line">{money(open.outstanding)}</span>
              </FieldRow>
              <FieldRow label={t.dashboard.invoices.due} empty={!open.due_date}>
                {open.due_date ? shortDate(open.due_date) : "—"}
              </FieldRow>
            </dl>

            {/* One primary, the next step for this bill: issue it while it
                has no PDF, take the payment while money is owed, give the
                receipt once it is paid (round 21E; ui-ux-pro-max
                primary-action). It had two at once for an unissued bill. */}
            {canUpdate && sheetPrimary(open) && (
              <div className="actions record-primary">
                {sheetPrimary(open) === "issue" && (
                  <button type="button" className="btn" data-variant="primary" disabled={busy} onClick={() => void issue(open)}>
                    {t.dashboard.invoices.issue}
                  </button>
                )}
                {sheetPrimary(open) === "pay" && (
                  <button type="button" className="btn" data-variant="primary" disabled={busy} onClick={() => setPaying(true)}>
                    {t.dashboard.invoices.recordPayment}
                  </button>
                )}
                {sheetPrimary(open) === "receipt" && (
                  <button type="button" className="btn" data-variant="primary" disabled={busy} onClick={() => void issueReceipt(open)}>
                    {t.dashboard.invoices.issueReceipt}
                  </button>
                )}
              </div>
            )}

            <div className="actions record-secondary">
              {open.generated_document_id && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void openDocument(String(open.generated_document_id))}
                >
                  {t.dashboard.invoices.openPdf}
                </button>
              )}
              {open.receipt_document_id && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void openDocument(String(open.receipt_document_id))}
                >
                  {t.dashboard.invoices.openReceipt}
                </button>
              )}
              {canUpdate && open.status !== "void" && open.status !== "paid" && sheetPrimary(open) !== "issue" && (
                <button type="button" className="btn" disabled={busy} onClick={() => void issue(open)}>
                  {open.generated_document_id ? t.dashboard.invoices.reissue : t.dashboard.invoices.issue}
                </button>
              )}
              {canUpdate && isOpen(open) && !paying && sheetPrimary(open) !== "pay" && (
                <button type="button" className="btn" disabled={busy} onClick={() => setPaying(true)}>
                  {t.dashboard.invoices.recordPayment}
                </button>
              )}
              {canUpdate && open.status === "paid" && sheetPrimary(open) !== "receipt" && (
                <button type="button" className="btn" disabled={busy} onClick={() => void issueReceipt(open)}>
                  {open.receipt_document_id ? t.dashboard.invoices.reissueReceipt : t.dashboard.invoices.issueReceipt}
                </button>
              )}
              {/* Handing the document over is not the primary action here —
                  issuing and recording payment already own that role — so
                  this is a plain button, never styled primary
                  (ui-ux-pro-max: primary-action). */}
              {canUpdate && (
                <button
                  type="button"
                  className="btn"
                  // undefined: the full invoice is still loading. null: the
                  // server could not ask LINE (21E re-review 2, I-2) —
                  // unknown, so the button stays pressable and the send
                  // route gives the true answer. Only a known "no" blocks.
                  disabled={
                    busy || open.customer_has_line === undefined || open.customer_has_line === false
                    || sendBlocked(open) !== null
                  }
                  onClick={() => void sendToCustomer(open.receipt_document_id ? "receipt" : "invoice")}
                >
                  {open.receipt_document_id ? t.dashboard.invoices.sendReceipt : t.dashboard.invoices.send}
                </button>
              )}
            </div>
            {/* A disabled send button says why in text a phone can read —
                never only a title= tooltip (ui-ux-pro-max: disabled-needs-
                a-reason). Only one reason applies at a time. Read straight
                off the invoice (ruling 18) — never a client-side lookup
                that could flash a stale answer while switching invoices. */}
            {canUpdate && sendBlocked(open) && (
              <p className="card-meta record-status-note">{sendBlocked(open)}</p>
            )}

            {/* Undoing a bill is not one of the things you do WITH it: its
                own row, under a rule, in the danger colour — and the word
                is "ยกเลิก", because a bill that went out is voided, never
                deleted (owner, 22 ก.ย. 2569). */}
            {canVoid && (open.status === "draft" || open.status === "issued") && Number(open.paid_amount) === 0 && (
              <div className="actions record-danger">
                <button type="button" className="btn" data-variant="danger" disabled={busy} onClick={() => void voidInvoice(open)}>
                  {t.dashboard.invoices.voidAction}
                </button>
              </div>
            )}

            {paying && (
              <form
                className="section"
                onSubmit={(event) => {
                  event.preventDefault();
                  void savePayment(open);
                }}
              >
                <div className="section-head">
                  <h2>{t.dashboard.invoices.recordPayment}</h2>
                </div>
                <dl className="fields">
                  <FieldRow label={t.dashboard.invoices.amount}>
                    {(id) => (
                      <input
                        id={id}
                        inputMode="decimal"
                        placeholder={money(open.outstanding)}
                        value={form.amount}
                        onChange={(event) => setForm({ ...form, amount: event.target.value })}
                      />
                    )}
                  </FieldRow>
                  <p className="hint">{t.dashboard.invoices.amountHint}</p>
                  <FieldRow label={t.dashboard.invoices.method}>
                    {(id) => (
                      <select
                        id={id}
                        value={form.method}
                        onChange={(event) => setForm({ ...form, method: event.target.value })}
                      >
                        {METHODS.map((m) => (
                          <option key={m} value={m}>{methodLabel(m)}</option>
                        ))}
                      </select>
                    )}
                  </FieldRow>
                  <FieldRow label={t.dashboard.invoices.paidAt}>
                    {(id) => (
                      <input
                        id={id}
                        type="date"
                        value={form.paid_at}
                        onChange={(event) => setForm({ ...form, paid_at: event.target.value })}
                      />
                    )}
                  </FieldRow>
                  <FieldRow label={t.dashboard.invoices.reference}>
                    {(id) => (
                      <input
                        id={id}
                        value={form.reference}
                        onChange={(event) => setForm({ ...form, reference: event.target.value })}
                      />
                    )}
                  </FieldRow>
                </dl>
                <div className="actions">
                  <button type="submit" className="btn" data-variant="primary" disabled={busy}>
                    {busy ? t.dashboard.working : t.dashboard.invoices.save}
                  </button>
                  <button type="button" className="btn" data-variant="quiet" disabled={busy} onClick={() => setPaying(false)}>
                    {t.dashboard.invoices.cancel}
                  </button>
                </div>
              </form>
            )}

            <section className="section">
              <div className="section-head">
                <h2>{t.dashboard.invoices.lines} ({lines.length})</h2>
                {/* "แก้ไขรายการ" turns the read-only list below into an
                    editable one, in place — it is not a second primary
                    action beside issue/record-payment above
                    (ui-ux-pro-max: primary-action). */}
                {editable && (
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    disabled={busy}
                    onClick={() => setEditingLines((value) => !value)}
                  >
                    {editingLines ? t.dashboard.invoices.editLinesDone : t.dashboard.invoices.editLines}
                  </button>
                )}
              </div>
              {!editable && (
                <p className="card-meta" style={{ marginBottom: 14 }}>{t.dashboard.invoices.editLocked}</p>
              )}
              {open.needs_reissue ? (
                <p className="callout" data-tone="warn" role="status">{t.dashboard.invoices.needsReissue}</p>
              ) : null}
              <ul className="list">
                {lines.map((line, index) => (
                  <li key={line.line_no} className="card">
                    <div className="card-title">{line.product_name}</div>
                    <div className="card-meta">
                      {line.qty} × <span className="money-line">{money(line.unit_price)}</span>
                      {" = "}
                      <span className="money-line">{money(line.line_total)}</span>
                      {line.notes ? ` · ${line.notes}` : ""}
                    </div>
                    {editable && editingLines && editingLineIndex !== index && (
                      <div className="card-actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          onClick={() => setEditingLineIndex(index)}
                          disabled={busy}
                        >
                          {t.common.edit}
                        </button>
                        {/* Deleting a line is its own per-line danger
                            action, under the list it changes — never
                            beside "ส่งให้ลูกค้า" above (ui-ux-pro-max:
                            destructive-nav-separation). Disabled on the
                            last remaining line — a bill must keep at
                            least one (review round 1, finding 1) — and
                            says why underneath, not only in a tooltip
                            (ui-ux-pro-max: disabled-needs-a-reason). */}
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          onClick={() => void removeLine(index)}
                          disabled={busy || lines.length <= 1}
                        >
                          {t.common.delete}
                        </button>
                      </div>
                    )}
                    {editable && editingLines && editingLineIndex !== index && lines.length <= 1 && (
                      <p className="card-meta record-status-note">{t.dashboard.invoices.oneLineRequired}</p>
                    )}
                    {editable && editingLines && editingLineIndex === index && (
                      <ProductLineForm
                        licenseId={licenseId}
                        token={token}
                        busy={busy}
                        initial={{
                          name: String(line.product_name ?? ""),
                          qty: Number(line.qty ?? 1),
                          price: String(line.unit_price ?? ""),
                        }}
                        onCancel={() => setEditingLineIndex(-1)}
                        onSubmit={(next) => saveLine(index, next)}
                      />
                    )}
                  </li>
                ))}
              </ul>
              {editable && editingLines && (
                addingLine ? (
                  <ProductLineForm
                    licenseId={licenseId}
                    token={token}
                    busy={busy}
                    onCancel={() => setAddingLine(false)}
                    onSubmit={addNewLine}
                  />
                ) : (
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    disabled={busy}
                    onClick={() => setAddingLine(true)}
                  >
                    {t.dashboard.deals.addProduct}
                  </button>
                )
              )}
              {totals && (
                <div className="totals">
                  <span>{t.dashboard.invoices.total}</span>
                  <strong>{money(totals.grand_total)}</strong>
                </div>
              )}
            </section>

            <section className="section">
              <div className="section-head">
                <h2>{t.dashboard.invoices.payments} ({(open.payments ?? []).length})</h2>
              </div>
              {(open.payments ?? []).length === 0 ? (
                <div className="empty">
                  <p>{t.dashboard.invoices.noPayments}</p>
                </div>
              ) : (
                <ul className="list">
                  {(open.payments ?? []).map((payment) => (
                    <li key={payment.id} className="card">
                      <div className="card-title">
                        <span className="money-line">{money(payment.amount)}</span>{" "}
                        <Badge stage="paid" label={methodLabel(payment.method)} />
                      </div>
                      <div className="card-meta">
                        {shortDate(payment.paid_at)}
                        {payment.reference ? ` · ${payment.reference}` : ""}
                        {payment.note ? ` · ${payment.note}` : ""}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <span hidden data-locale={locale} />
          </>
        )}
      </Sheet>

      <InvoiceCreateSheet
        open={creating}
        onClose={() => setCreating(false)}
        token={token}
        licenseId={licenseId}
        prefill={prefill}
        onCreated={created}
        say={say}
      />

      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={busy} />
    </SalesShell>
  );
}
