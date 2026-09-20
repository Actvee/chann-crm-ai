"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { BulkPaste } from "../_bulk-paste";
import { CsvImport } from "../_csv-import";
import { Badge, Count, Empty } from "../_components";
import { ConfirmDialog, useConfirm } from "../../_confirm";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { ListFilters } from "../../_filters";
import { usePagedList } from "../../_paged-list";
import { InlineCreateForm } from "../../_inline-create";
import {
  ListControls, byNewest, byOldest, useListControls,
} from "../../_list-controls";

import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";

type Customer = {
  id: string;
  customer_id: string;
  first_name?: string | null;
  last_name?: string | null;
  stage: string;
  phone?: string | null;
  email?: string | null;
  created_at?: string | null;
  customer_chann_uid?: string | null;
};

function fullName(customer: Customer): string {
  return [customer.first_name, customer.last_name].filter(Boolean).join(" ") || "—";
}

export default function CustomerList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const s = useSalesText();
  const failureText = useFailureText();
  // Customer stages are only two, and both already have names in the
  // dictionary under their own sections.
  const stageLabel = (stage: string) =>
    stage === "contact" ? t.customer.title : t.customer.lead;
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [busyId, setBusyId] = useState("");
  const [stage, setStage] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  // Searched and paged by the SERVER. It used to fetch the list and filter
  // it here, which cannot find the 600th customer of 800 behind a cap of
  // 500 — the cap hid records rather than slowing the page (20 ก.ย. 2569).
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
  const list = usePagedList<Customer>({
    token, licenseId, ready: session.ready,
    path: `licenses/${licenseId}/customers`,
    params: { stage },
    onError: listError,
  });
  const customers = list.rows;
  const totalHeld = list.total;
  const load = list.reload;

  useEffect(() => {
    if (session.ready && !list.busy) say("");
  }, [session.ready, list.busy, say]);

  async function createCustomer(values: Record<string, string>) {
    if (values.phone && !/^\+?[\d\s\-().]+$/.test(values.phone)) {
      say(t.dashboard.customers.phoneLetters, "error");
      throw new Error("phone");
    }
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify(values),
        },
      );
      if (!response.ok) {
        // The API's own reason, in the reader's language — a duplicate
        // names the record that already holds the number, a 422 names
        // the field. Thrown so the form keeps what was typed (C3).
        const why = await failureText(response);
        say(
          response.status === 422 ? `${why} — ${s.customers.lastNameAndPhone}` : why,
          "error",
        );
        throw new Error(why);
      }
      await load();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  /** User review (4 Sep 2026): delete a lead — the platform's soft delete,
   *  confirmed first, behind customer.archive. */
  async function archive(customer: Customer) {
    // Named, with its code, with what survives it, and with the fact that
    // this archives rather than erases — window.confirm could say none of
    // that (round 20d).
    const copy = t.dashboard.customers;
    const ok = await ask({
      action: copy.archiveAction,
      target: fullName(customer),
      code: customer.customer_id,
      affects: [copy.archiveAlsoDeals, copy.archiveAlsoHistory],
      reversible: copy.archiveKeeps,
      confirmLabel: copy.archive,
    });
    if (!ok) return;
    setBusyId(customer.id);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers/${customer.id}/archive`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(response.status === 403 ? t.dashboard.customers.archiveDenied : await failureText(response), "error");
        return;
      }
      list.setRows((rows) => rows.filter((row) => row.id !== customer.id));
      // The success says WHAT went, by name and code — "saved" tells the
      // person nothing they can check.
      say(
        t.dashboard.customers.archivedNamed
          .replace("{name}", fullName(customer))
          .replace("{code}", customer.customer_id),
        "ok",
      );
    } catch {
      say(t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  async function promote(customer: Customer) {
    setBusyId(customer.id);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers/${customer.id}/promote`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(
          response.status === 403 ? t.dashboard.customers.promoteDenied : await failureText(response),
          "error",
        );
        return;
      }
      say(`${fullName(customer)} — ${t.dashboard.customers.promoted}`, "ok");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  // The rows ARE the answer: the stage and the search term were applied by
  // the database, so there is nothing left to filter here.
  const searched = customers;

  const sorts = [
    { key: "newest", label: t.dashboard.list.newest, compare: byNewest<Customer> },
    { key: "oldest", label: t.dashboard.list.oldest, compare: byOldest<Customer> },
    {
      key: "name",
      label: t.dashboard.list.byName,
      compare: (a: Customer, b: Customer) => fullName(a).localeCompare(fullName(b), "th"),
    },
  ];
  const controls = useListControls(searched, sorts, "newest");
  const visible = controls.visible;
  // Every control is gated on the key its route actually checks (C7);
  // a suspended shop offers none of them (C4).
  const can = (key: string) => !session.suspended && permissions.has(key);

  /** Round 19g: open the conversation with a customer who is linked on LINE. */
  async function chatWith(customer: Customer) {
    if (!customer.customer_chann_uid) return;
    setBusyId(customer.id);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/chat-sessions/start`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ customer_chann_uid: customer.customer_chann_uid }),
      });
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const opened = (await response.json()) as { id?: string };
      window.location.assign(`/liff/sales/chats${opened.id ? `?session=${encodeURIComponent(opened.id)}` : ""}`);
    } catch {
      say(t.dashboard.loadFailed, "error");
    } finally {
      setBusyId("");
    }
  }

  return (
    <SalesShell
      session={session}
      title={t.customer.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <ListFilters
        query={list.query}
        onQuery={list.setQuery}
        placeholder={t.dashboard.customers.searchHint}
        status={stage}
        statuses={[
          { value: "lead", label: t.customer.lead },
          { value: "contact", label: t.customer.title },
        ]}
        onStatus={setStage}
      />

      <ListControls
        sorts={sorts}
        sortKey={controls.sortKey}
        onSort={controls.setSortKey}
        from={controls.from}
        to={controls.to}
        onFrom={controls.setFrom}
        onTo={controls.setTo}
      />

      <Count shown={visible.length} total={customers.length} />
      {totalHeld !== null && totalHeld > customers.length && (
        <p className="hint">
          {t.dashboard.showingOf
            .replace("{shown}", String(customers.length))
            .replace("{total}", String(totalHeld))}
        </p>
      )}
      {list.hasMore && (
        <div className="actions">
          <button type="button" className="btn" disabled={list.busy} onClick={list.loadMore}>
            {list.busy ? t.dashboard.opening : t.dashboard.list.loadMore}
          </button>
        </div>
      )}

      {/* One row, two doors. Both used to be open sections on the page
          (owner, 20 ก.ย. 2569). */}
      {can("customer.create") && (
        <div className="actions">
          <BulkPaste token={token} licenseId={licenseId} onDone={() => void load()} />
          <CsvImport kind="customers" token={token} licenseId={licenseId} onDone={() => void load()} />
        </div>
      )}

      {can("customer.create") && (
        <InlineCreateForm
          title={t.dashboard.customers.add}
          busy={busy}
          fields={[
            // What the API requires (Phase 9): a last name and a phone.
            // The old form demanded a first name instead and every
            // "สมชาย + phone" ended in a 422 (review C3).
            { name: "first_name", label: t.dashboard.fields.firstName },
            { name: "last_name", label: t.dashboard.fields.lastName, required: true },
            { name: "phone", label: t.dashboard.fields.phone, type: "tel", required: true },
            { name: "email", label: t.dashboard.fields.email },
          ]}
          onSubmit={createCustomer}
        />
      )}

      {visible.length === 0 ? (
        <Empty
          message={
            list.searching
              ? t.dashboard.opening
              : list.query
                ? `${t.dashboard.customers.noMatch}: “${list.query}”`
                : t.dashboard.customers.empty
          }
        />
      ) : (
        <ul className="list">
          {visible.map((customer) => (
            <li key={customer.id} className="card" data-stage={customer.stage}>
              {/* The whole row opens the detail view — a list you cannot
                  drill into is a report, not a tool. `.row-link` rather
                  than a hand-written `textDecoration: none`: three lists
                  had three copies of the same two inline properties and
                  none of them drew the chevron that tells a person the
                  row is a door (owner, 18 ก.ย. 2569). */}
              <Link className="row-link" href={`/liff/sales/customers/${customer.id}`}>
                <span className="row-body">
                  <span className="card-title">
                    {fullName(customer)}
                    <Badge stage={customer.stage} label={stageLabel(customer.stage)} />
                  </span>
                  <span className="card-meta">
                    <span className="code">{customer.customer_id}</span>
                    {customer.phone ? ` · ${customer.phone}` : ""}
                    {customer.email ? ` · ${customer.email}` : ""}
                  </span>
                </span>
              </Link>
              {customer.customer_chann_uid && can("chat_session.reply") && (
                <div className="card-actions">
                  <button
                    type="button"
                    className="btn"
                    disabled={busyId === customer.id}
                    onClick={() => void chatWith(customer)}
                  >
                    {t.dashboard.chats.startWithCustomer}
                  </button>
                </div>
              )}
              {customer.stage === "lead" && (can("customer.update") || can("customer.archive")) && (
                <div className="card-actions">
                  {can("customer.update") && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="primary"
                      onClick={() => void promote(customer)}
                      disabled={busyId === customer.id}
                    >
                      {busyId === customer.id ? t.dashboard.saving : t.dashboard.customers.promote}
                    </button>
                  )}
                  {can("customer.archive") && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="danger"
                      onClick={() => void archive(customer)}
                      disabled={busyId === customer.id}
                    >
                      {t.dashboard.customers.archive}
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={Boolean(busyId)} />
    </SalesShell>
  );
}
