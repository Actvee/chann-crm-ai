"use client";

import { useCallback, useEffect, useState } from "react";

import { FieldRow } from "../../_field-row";
import { Count, Empty } from "../_components";
import { CsvImport } from "../_csv-import";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { useFailureText, useFormatters } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";

// Mirrors ProductOut exactly. The earlier version declared `name`, which
// the API has never returned — it sends `product_name` — so every row
// rendered its fallback dash while the price beside it worked fine.
//
// TypeScript cannot catch this: the shape is asserted on a JSON response,
// so a field that does not exist is simply undefined at runtime. The only
// defence is writing the type from the schema rather than from memory.
type Product = {
  id: string;
  product_id: string;
  product_name: string;
  sku?: string | null;
  category?: string | null;
  unit_price?: string | number | null;
  description?: string | null;
};

// The Data tier's cap; asked for explicitly (review C10) rather than
// taking a default of 200 that hid the rest of a bigger catalogue.
const PAGE = 1000;

const BLANK = {
  product_id: "", product_name: "", unit_price: "", category: "", sku: "", description: "",
};

export default function ProductList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const s = useSalesText();
  const { money } = useFormatters();
  const failureText = useFailureText();
  const [products, setProducts] = useState<Product[]>([]);
  const [adding, setAdding] = useState(false);
  // Editing an existing row locks its code (review C19): the save is an
  // upsert keyed on product_id, so a changed code created a second
  // product instead of correcting the first.
  const [editingExisting, setEditingExisting] = useState(false);
  const [draft, setDraft] = useState(BLANK);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [query, setQuery] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/products?limit=${PAGE}`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    setProducts((await response.json()) as Product[]);
    say("");
  }, [licenseId, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [session.ready, load, say, t]);

  const needle = query.trim().toLowerCase();
  const visible = needle
    ? products.filter((product) =>
        [product.product_name, product.product_id, product.sku]
          .map((value) => String(value ?? "").toLowerCase())
          .some((value) => value.includes(needle)),
      )
    : products;

  async function saveProduct() {
    // Both are required by the Data tier. Catching it here means the
    // person is told which field, rather than getting a 4xx naming a key
    // they never saw.
    if (!draft.product_id.trim() || !draft.product_name.trim()) {
      say(t.dashboard.products.needsCodeAndName, "error");
      return;
    }
    setSaving(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/products/${encodeURIComponent(draft.product_id.trim())}`,
        {
          method: "PUT",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            product_id: draft.product_id.trim(),
            product_name: draft.product_name.trim(),
            unit_price: draft.unit_price.trim() || null,
            category: draft.category.trim() || null,
            sku: draft.sku.trim() || null,
            description: draft.description.trim() || null,
          }),
        },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      say(t.dashboard.saved, "ok");
      setDraft(BLANK);
      setAdding(false);
      setEditingExisting(false);
      await load();
    } finally {
      setSaving(false);
    }
  }

  // The list itself needs only product.read (review C8); changing it
  // needs product.manage. A suspended shop changes nothing.
  const canManage = !session.suspended && permissions.has("product.manage");

  return (
    <SalesShell
      session={session}
      title={t.product.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <label className="field">
        <span>{t.dashboard.search}</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t.dashboard.products.searchHint}
          type="search"
        />
      </label>

      <Count shown={visible.length} total={products.length} />
      {products.length >= PAGE && (
        <p className="count">{s.errors.showingLatest.replace("{count}", String(products.length))}</p>
      )}

      {canManage && (
        <CsvImport kind="products" token={token} licenseId={licenseId} onDone={() => load()} />
      )}

      {!canManage && !session.suspended && session.ready && (
        <p className="card-meta" style={{ marginBottom: 12 }}>{s.products.readOnly}</p>
      )}

      {canManage && (
        <section className="section" style={{ margin: "12px 0 16px" }}>
          <div className="section-head">
            <h2>{editingExisting ? t.common.edit : t.dashboard.products.add}</h2>
            {!adding && (
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => {
                  setDraft(BLANK);
                  setEditingExisting(false);
                  setAdding(true);
                }}
              >
                {t.dashboard.products.add}
              </button>
            )}
          </div>
          {adding && (
            <dl className="fields">
              {([
                ["product_id", t.dashboard.products.code, "FAN001"],
                ["product_name", t.product.title, ""],
                ["unit_price", t.dashboard.products.price, "0.00"],
                ["category", t.dashboard.products.category, ""],
                ["sku", t.dashboard.products.sku, ""],
                ["description", t.dashboard.products.description, ""],
              ] as const).map(([field, label, placeholder]) => (
                <FieldRow key={field} label={label}>
                  {(id) => (
                    <input
                      id={id}
                      value={draft[field]}
                      placeholder={placeholder}
                      disabled={field === "product_id" && editingExisting}
                      inputMode={field === "unit_price" ? "decimal" : undefined}
                      onChange={(event) =>
                        setDraft({ ...draft, [field]: event.target.value })
                      }
                    />
                  )}
                </FieldRow>
              ))}
              {editingExisting && (
                <p className="card-meta" style={{ margin: "0 0 8px" }}>{s.products.codeLocked}</p>
              )}
              <div className="actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => {
                    setAdding(false);
                    setEditingExisting(false);
                  }}
                  disabled={saving}
                >
                  {t.common.cancel}
                </button>
                <button
                  type="button"
                  className="btn"
                  data-variant="primary"
                  onClick={() => void saveProduct()}
                  disabled={saving}
                >
                  {saving ? t.dashboard.saving : t.common.save}
                </button>
              </div>
            </dl>
          )}
        </section>
      )}

      {visible.length === 0 ? (
        <Empty
          message={
            products.length === 0
              ? t.dashboard.products.empty
              : `${t.dashboard.products.noMatch}: “${query}”`
          }
        />
      ) : (
        <ul className="list">
          {visible.map((product) => (
            <li key={product.id} className="card">
              <div className="card-title">{product.product_name || "—"}</div>
              <div className="card-meta">
                <span className="code">
                  {product.product_id || product.sku || t.dashboard.products.noSku}
                </span>
                {product.unit_price != null && product.unit_price !== ""
                  ? ` · ${money(product.unit_price)}`
                  : ` · ${t.dashboard.products.noPrice}`}
              </div>
              {product.description && (
                <div className="card-meta">{product.description}</div>
              )}
              {/* Editing an existing product. The save is an upsert keyed
                  on product_id, so the form already handled edits — it
                  just had no way to be filled with a product's current
                  values, which made it a create-only form in practice. */}
              {canManage && (
                <div className="card-actions">
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    onClick={() => {
                      setDraft({
                        product_id: String(product.product_id ?? product.sku ?? ""),
                        product_name: String(product.product_name ?? ""),
                        unit_price:
                          product.unit_price != null ? String(product.unit_price) : "",
                        category: String(product.category ?? ""),
                        sku: String(product.sku ?? ""),
                        description: String(product.description ?? ""),
                      });
                      setEditingExisting(true);
                      setAdding(true);
                      window.scrollTo({ top: 0, behavior: "smooth" });
                    }}
                  >
                    {t.common.edit}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </SalesShell>
  );
}
