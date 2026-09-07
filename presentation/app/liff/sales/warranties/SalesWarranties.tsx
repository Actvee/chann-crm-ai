"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { CsvImport } from "../_csv-import";
import { FieldRow } from "../../_field-row";
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
  warranty_start?: string | null;
  warranty_end?: string | null;
  status?: string | null;
};

type Product = { id: string; product_name: string; product_id?: string | null };
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

  const [rows, setRows] = useState<Warranty[]>([]);
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

  const [serial, setSerial] = useState("");
  const [productId, setProductId] = useState("");
  const [contactId, setContactId] = useState("");
  const [start, setStart] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  const load = useCallback(
    async (serialNumber = "") => {
      if (!token || !licenseId) return;
      const headers = proxyHeaders(token, licenseId);
      const url = serialNumber
        ? `/api/phase2/licenses/${licenseId}/warranties?serial_number=${encodeURIComponent(serialNumber)}`
        : `/api/phase2/licenses/${licenseId}/warranties?limit=${PAGE}`;
      const response = await fetch(url, { headers });
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      const found = (await response.json()) as Warranty[];
      setRows(found);
      setSearchedSerial(serialNumber);
      if (serialNumber && found.length === 0) {
        say(s.warranties.serialNotFound.replace("{serial}", serialNumber), "error");
      }
    },
    [token, licenseId, t, s, say],
  );

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
      say(copy.registered, "ok");
      await load();
    } catch {
      say(copy.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  const canCreate = !session.suspended && permissions.has("warranty.create");

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
            {copy.title} ({rows.length})
          </h2>
        </div>
        {!searchedSerial && rows.length >= PAGE && (
          <p className="count">{s.errors.showingLatest.replace("{count}", String(rows.length))}</p>
        )}
        {rows.length === 0 ? (
          <div className="empty">
            <p>{copy.empty}</p>
          </div>
        ) : (
          <ul className="list">
            {rows.map((row) => (
              <li key={row.id} className="card">
                <div className="card-title">
                  {row.serial_number}
                  {row.product_name ? ` · ${row.product_name}` : ""}
                  <span
                    className="badge"
                    data-tone={row.customer_chann_uid ? "ok" : undefined}
                    style={{ marginLeft: 8 }}
                  >
                    {row.customer_chann_uid ? copy.claimed : copy.unclaimed}
                  </span>
                </div>
                <div className="card-meta">
                  {row.warranty_number}
                  {row.warranty_end ? ` · ${copy.expires} ${shortDate(row.warranty_end, locale)}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </SalesShell>
  );
}
