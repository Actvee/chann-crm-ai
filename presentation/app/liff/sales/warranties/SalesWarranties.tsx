"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { CsvImport } from "../_csv-import";
import { FieldRow } from "../../_field-row";
import { ListFilters, optionsFrom } from "../../_filters";
import { usePagedList } from "../../_paged-list";
import { shortDate } from "../../_list-controls";
import { PickerOption, SearchablePicker } from "../../_searchable-picker";
import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";

type Warranty = {
  id: string;
  warranty_number?: string | null;
  serial_number?: string | null;
  product_name?: string | null;
  customer_chann_uid?: string | null;
  contact_id?: string | null;
  contact_name?: string | null;
  contact_code?: string | null;
  warranty_start?: string | null;
  warranty_end?: string | null;
  product_id?: string | null;
  status?: string | null;
};

type Product = {
  id: string;
  product_name: string;
  product_id?: string | null;
  // The period a unit of this product inherits when nobody types one
  // (owner, 16 ก.ย. 2569: "เริ่มต้นจะอิงตามแต่ละสินค้า").
  warranty_months?: number | null;
};
type Customer = {
  id: string;
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
  customer_id?: string | null;
};

// The Data tier's cap for the book; explicit so the page knows what it
// asked for (review C10) and can say when the shop has more.
const PAGE = 500;

/**
 * The shop's book of sold units (Phase 7.5, the staff half).
 *
 * Owner rule, 3 Sep: a customer cannot invent a serial. The shop records
 * the unit here (or in chat: "ลงทะเบียนสินค้า SN… แอร์ ให้ลูกค้า สมชาย"),
 * and the customer types the sticker in the customer LINE or app to be
 * attached to it — after which faults can be filed against the machine.
 * Until this page existed there was no staff surface for warranties at
 * all, so the parity rule (chat ⇄ UI) was broken on the shop side too.
 */
export default function SalesWarranties({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const s = useSalesText();
  const failureText = useFailureText();
  const copy = t.dashboard.warranties;

  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<PickerOption[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  // A serial looked up on the server (review C10): the list shows the
  // latest units, and a unit sold two years ago is found by its sticker,
  // not by scrolling.
  const [serialQuery, setSerialQuery] = useState("");
  const [searchedSerial, setSearchedSerial] = useState("");
  // The serial search above asks the server for one sticker anywhere in
  // the book; these narrow the page already loaded, by anything on a row.
  const [statusFilter, setStatusFilter] = useState("");

  const [serial, setSerial] = useState("");
  const [productId, setProductId] = useState("");
  const [contactId, setContactId] = useState("");
  const [start, setStart] = useState("");
  const [months, setMonths] = useState("");
  // Round 19g: a unit registered without its purchase date gets it here.
  const [dateFor, setDateFor] = useState<Record<string, string>>({});
  // Round 19t: an existing registration can be corrected — the purchase
  // date, the period, or the end date itself.
  const [editing, setEditing] = useState("");
  const [editStart, setEditStart] = useState("");
  const [editMonths, setEditMonths] = useState("");
  const [editEnd, setEditEnd] = useState("");

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
  // Two questions, one list. `serial_number` is the exact lookup — "this
  // unit" — and `q` is the shop searching its own book; both are answered
  // by the database now, so neither is limited to the rows that happened
  // to be on the page (round 20N).
  const list = usePagedList<Warranty>({
    token, licenseId, ready: session.ready,
    path: `licenses/${licenseId}/warranties`,
    params: { status: statusFilter, serial_number: searchedSerial },
    onError: listError,
  });
  const rows = list.rows;

  const load = useCallback(
    async (serialNumber = "") => {
      // Changing the serial refetches through the hook; same call shape as
      // before so every caller below is untouched.
      setSearchedSerial(serialNumber);
      if (serialNumber === searchedSerial) await list.reload();
    },
    [list, searchedSerial],
  );

  // Said once per answer, not once per render.
  useEffect(() => {
    if (!searchedSerial || list.busy) return;
    if (rows.length === 0) {
      say(s.warranties.serialNotFound.replace("{serial}", searchedSerial), "error");
    }
  }, [searchedSerial, rows.length, list.busy, s, say]);

  const loadPickers = useCallback(async () => {
    if (!token || !licenseId) return;
    const headers = proxyHeaders(token, licenseId);
    // The pickers are secondary: a failure there leaves free entry.
    const [productsRes, customersRes] = await Promise.all([
      fetch(`/api/phase2/licenses/${licenseId}/products?limit=1000`, { headers }),
      fetch(`/api/phase2/licenses/${licenseId}/customers`, { headers }),
    ]);
    if (productsRes.ok) setProducts((await productsRes.json()) as Product[]);
    if (customersRes.ok) {
      const list = (await customersRes.json()) as Customer[];
      setCustomers(
        list.map((c) => ({
          value: c.id,
          label: [c.first_name, c.last_name].filter(Boolean).join(" ") || c.id,
          // customer_id, the code that exists (review C13): the picker
          // searched a `customer_code` no API has ever sent.
          keywords: [c.phone, c.customer_id].filter(Boolean).join(" "),
        })),
      );
    }
  }, [licenseId, token]);

  useEffect(() => {
    if (!session.ready) return;
    void (async () => {
      try {
        await load();
        if (permissions.has("warranty.create")) await loadPickers();
        say("");
      } catch (error) {
        say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error");
      }
    })();
  }, [session.ready, load, loadPickers, permissions, say, t]);

  /** The period this unit's product hands down, when it says one. */
  function productMonths(row: Warranty): number | null {
    const product = products.find(
      (p) => p.id === row.product_id || (!!row.product_name && p.product_name === row.product_name),
    );
    return product?.warranty_months ?? null;
  }

  async function saveCover(row: Warranty) {
    if (editEnd && editStart && editEnd < editStart) {
      say(copy.endBeforeStart, "error");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/warranties/${row.id}`, {
        method: "PATCH",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({
          warranty_start: editStart || undefined,
          warranty_months: editMonths.trim() ? Number(editMonths.trim()) : undefined,
          warranty_end: editEnd || undefined,
        }),
      });
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      say(copy.warrantySaved, "ok");
      setEditing("");
      await load();
    } catch {
      say(copy.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function setPurchaseDate(row: Warranty) {
    const chosen = dateFor[row.id];
    if (!chosen) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/warranties/${row.id}`, {
        method: "PATCH",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ warranty_start: chosen }),
      });
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      say(copy.purchaseDateSaved, "ok");
      await load();
    } catch {
      say(copy.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function register() {
    if (!serial.trim()) return;
    setBusy(true);
    try {
      const product = products.find((p) => p.id === productId);
      const response = await fetch(`/api/phase2/licenses/${licenseId}/warranties`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({
          serial_number: serial.trim(),
          product_id: productId || undefined,
          product_name: product?.product_name,
          contact_id: contactId || undefined,
          warranty_start: start || undefined,
          warranty_months: months.trim() ? Number(months.trim()) : undefined,
        }),
      });
      if (response.status === 409) {
        say(copy.duplicate, "error");
        return;
      }
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      setSerial("");
      setProductId("");
      setContactId("");
      setStart("");
      setMonths("");
      say(copy.registered, "ok");
      await load();
    } catch {
      say(copy.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  const canCreate = !session.suspended && permissions.has("warranty.create");
  // Status, serial and search were all applied by the database.
  const visible = rows;

  return (
    <SalesShell
      session={session}
      title={copy.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="page-intro">{copy.intro}</p>

      <label className="field">
        <span>{s.warranties.serialSearch}</span>
        <input
          type="search"
          value={serialQuery}
          autoCapitalize="characters"
          placeholder={s.warranties.serialSearchHint}
          onChange={(event) => setSerialQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void load(serialQuery.trim()).catch(() => undefined);
          }}
        />
      </label>
      <div className="actions" style={{ marginBottom: 14 }}>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          disabled={!serialQuery.trim()}
          onClick={() => void load(serialQuery.trim()).catch(() => undefined)}
        >
          {s.warranties.search}
        </button>
        {searchedSerial && (
          <button
            type="button"
            className="btn"
            data-variant="quiet"
            onClick={() => {
              setSerialQuery("");
              void load().then(() => say("")).catch(() => undefined);
            }}
          >
            {s.warranties.clearSearch}
          </button>
        )}
      </div>

      {canCreate && (
        <CsvImport kind="warranties" token={token} licenseId={licenseId} onDone={() => load()} />
      )}

      {canCreate && (
        <section className="section">
          <div className="section-head">
            <h2>{copy.register}</h2>
          </div>
          <dl className="fields">
            <FieldRow label={copy.serial}>
              {(id) => (
                <input
                  id={id}
                  value={serial}
                  autoCapitalize="characters"
                  onChange={(e) => setSerial(e.target.value)}
                />
              )}
            </FieldRow>
            <FieldRow label={copy.product}>
              {(id) => (
                <select id={id} value={productId} onChange={(e) => setProductId(e.target.value)}>
                  <option value="">{copy.productNone}</option>
                  {products.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.product_name}
                      {p.product_id ? ` (${p.product_id})` : ""}
                    </option>
                  ))}
                </select>
              )}
            </FieldRow>
            <FieldRow label={copy.customer}>
              {(id) => (
                <SearchablePicker
                  id={id}
                  options={customers}
                  value={contactId}
                  placeholder={copy.customerNone}
                  onChange={setContactId}
                />
              )}
            </FieldRow>
            <FieldRow label={copy.warrantyStart}>
              {(id) => (
                <input id={id} type="date" value={start} onChange={(e) => setStart(e.target.value)} />
              )}
            </FieldRow>
            <FieldRow label={copy.warrantyMonths}>
              {(id) => (
                <input id={id} type="number" min={1} value={months} placeholder={copy.warrantyMonthsHint} onChange={(e) => setMonths(e.target.value)} />
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={busy || !serial.trim()}
                onClick={() => void register()}
              >
                {busy ? t.dashboard.related.saving : copy.register}
              </button>
            </div>
          </dl>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2>
            {copy.title} ({visible.length})
          </h2>
        </div>
        <ListFilters
          query={list.query}
          onQuery={list.setQuery}
          status={statusFilter}
          statuses={optionsFrom(copy.status as Record<string, string>)}
          onStatus={setStatusFilter}
        />
        {list.total !== null && list.total > rows.length && (
          <p className="count">
            {t.dashboard.showingOf
              .replace("{shown}", String(rows.length))
              .replace("{total}", String(list.total))}
          </p>
        )}
        {list.hasMore && (
          <div className="actions">
            <button type="button" className="btn" disabled={list.busy} onClick={list.loadMore}>
              {list.busy ? t.dashboard.opening : t.dashboard.list.loadMore}
            </button>
          </div>
        )}
        {visible.length === 0 ? (
          <div className="empty">
            <p>
              {list.searching
                ? t.dashboard.opening
                : list.query || statusFilter || searchedSerial
                  ? t.dashboard.noMatch
                  : copy.empty}
            </p>
          </div>
        ) : (
          <ul className="list">
            {visible.map((row) => (
              <li key={row.id} className="card">
                <div className="card-title">
                  {row.serial_number}
                  {row.product_name ? ` · ${row.product_name}` : ""}
                  <span
                    className="badge"
                    data-tone={row.customer_chann_uid ? "ok" : undefined}
                    style={{ marginLeft: 8 }}
                  >
                    {row.contact_name
                      ? `${copy.contact} ${row.contact_name}${row.contact_code ? ` (${row.contact_code})` : ""} · ${row.customer_chann_uid ? copy.lineLinked : copy.lineNotLinked}`
                      : row.customer_chann_uid ? copy.claimed : copy.unclaimed}
                  </span>
                </div>
                <div className="card-meta">
                  {row.warranty_number}
                  {row.warranty_end ? ` · ${copy.expires} ${shortDate(row.warranty_end, locale)}` : ` · ${copy.noPurchaseDate}`}
                </div>
                {!row.warranty_start && permissions.has("warranty.update") && (
                  <div className="actions">
                    <input
                      type="date"
                      aria-label={copy.warrantyStart}
                      value={dateFor[row.id] ?? ""}
                      onChange={(e) => setDateFor({ ...dateFor, [row.id]: e.target.value })}
                    />
                    <button
                      type="button"
                      className="btn"
                      disabled={busy || !dateFor[row.id]}
                      onClick={() => void setPurchaseDate(row)}
                    >
                      {copy.setPurchaseDate}
                    </button>
                  </div>
                )}
                {/* A registration with a date was read-only: the period and
                    the end could not be changed at all, so nobody could try
                    an expiry (owner's tester, 16 ก.ย. 2569). */}
                {row.warranty_start && permissions.has("warranty.update") && (
                  <div className="actions">
                    <button
                      type="button"
                      className="btn"
                      data-variant="quiet"
                      aria-expanded={editing === row.id}
                      onClick={() => {
                        setEditing(editing === row.id ? "" : row.id);
                        setEditStart(row.warranty_start ?? "");
                        setEditEnd(row.warranty_end ?? "");
                        setEditMonths("");
                      }}
                    >
                      {copy.edit}
                    </button>
                  </div>
                )}
                {editing === row.id && (
                  <dl className="fields">
                    <FieldRow label={copy.warrantyStart}>
                      {(id) => (
                        <input
                          id={id}
                          type="date"
                          value={editStart}
                          onChange={(e) => setEditStart(e.target.value)}
                        />
                      )}
                    </FieldRow>
                    <FieldRow label={copy.warrantyMonths}>
                      {(id) => (
                        <>
                          <input
                            id={id}
                            inputMode="numeric"
                            placeholder={String(productMonths(row) ?? "")}
                            value={editMonths}
                            onChange={(e) => setEditMonths(e.target.value)}
                          />
                          <span className="hint">
                            {productMonths(row)
                              ? copy.fromProduct.replace("{months}", String(productMonths(row)))
                              : copy.warrantyMonthsHint}
                          </span>
                        </>
                      )}
                    </FieldRow>
                    <FieldRow label={copy.warrantyEnd}>
                      {(id) => (
                        <>
                          <input
                            id={id}
                            type="date"
                            value={editEnd}
                            onChange={(e) => setEditEnd(e.target.value)}
                          />
                          <span className="hint">{copy.editHint}</span>
                        </>
                      )}
                    </FieldRow>
                    <div className="actions">
                      <button
                        type="button"
                        className="btn"
                        data-variant="primary"
                        disabled={busy}
                        onClick={() => void saveCover(row)}
                      >
                        {copy.saveWarranty}
                      </button>
                    </div>
                  </dl>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </SalesShell>
  );
}
