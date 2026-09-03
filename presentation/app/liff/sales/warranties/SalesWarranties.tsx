"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../_components";
import { CsvImport } from "../_csv-import";
import { FieldRow } from "../../_field-row";
import { shortDate } from "../../_list-controls";
import { PickerOption, SearchablePicker } from "../../_searchable-picker";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

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
  customer_code?: string | null;
};

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
  const { t } = useLanguage();
  const copy = t.dashboard.warranties;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [rows, setRows] = useState<Warranty[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<PickerOption[]>([]);
  const [canCreate, setCanCreate] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);

  const [serial, setSerial] = useState("");
  const [productId, setProductId] = useState("");
  const [contactId, setContactId] = useState("");
  const [start, setStart] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const headers = proxyHeaders(currentToken, license);
      const response = await fetch(`/api/phase2/licenses/${license}/warranties`, { headers });
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      setRows((await response.json()) as Warranty[]);
    },
    [token, licenseId, t],
  );

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setToken(session.token);
      setLicenseId(license);
      const permissions = await fetchPermissions(session.token, license);
      setCanCreate(permissions.has("warranty.create"));
      await load(session.token, license);
      const headers = proxyHeaders(session.token, license);
      // The pickers are secondary: a failure there leaves free entry.
      const [productsRes, customersRes] = await Promise.all([
        fetch(`/api/phase2/licenses/${license}/products`, { headers }),
        fetch(`/api/phase2/licenses/${license}/customers`, { headers }),
      ]);
      if (productsRes.ok) setProducts((await productsRes.json()) as Product[]);
      if (customersRes.ok) {
        const list = (await customersRes.json()) as Customer[];
        setCustomers(
          list.map((c) => ({
            value: c.id,
            label: [c.first_name, c.last_name].filter(Boolean).join(" ") || c.id,
            keywords: [c.phone, c.customer_code].filter(Boolean).join(" "),
          })),
        );
      }
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

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
      if (!response.ok) throw new Error(String(response.status));
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

  return (
    <AppShell
      title={copy.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="page-intro">{copy.intro}</p>

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
                  {row.warranty_end ? ` · ${copy.expires} ${shortDate(row.warranty_end)}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </AppShell>
  );
}
