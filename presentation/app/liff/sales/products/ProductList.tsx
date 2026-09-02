"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell, CompanyPicker, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Membership, fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

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

export default function ProductList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({
    product_id: "", product_name: "", unit_price: "", category: "", sku: "", description: "",
  });
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [query, setQuery] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/products`, {
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
  }, [licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [licenseId, load, say, token]);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      setToken(session.token);
      setMemberships(session.memberships);
      setLicenseId(session.memberships[0]?.license_id ?? "");
      setPermissions(
        await fetchPermissions(session.token, session.memberships[0]?.license_id ?? ""),
      );
      if (!session.memberships.length) say(t.liff.noCompany, "error");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say]);

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
        say(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      say(t.dashboard.saved, "ok");
      setDraft({ product_id: "", product_name: "", unit_price: "", category: "", sku: "", description: "" });
      setAdding(false);
      await load();
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell
      title={t.product.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

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

      {permissions.has("product.manage") && (
        <section className="section" style={{ margin: "12px 0 16px" }}>
          <div className="section-head">
            <h2>{t.dashboard.products.add}</h2>
            {!adding && (
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => setAdding(true)}
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
                <div className="field-row" key={field}>
                  <dt>{label}</dt>
                  <dd>
                    <input
                      value={draft[field]}
                      placeholder={placeholder}
                      inputMode={field === "unit_price" ? "decimal" : undefined}
                      onChange={(event) =>
                        setDraft({ ...draft, [field]: event.target.value })
                      }
                    />
                  </dd>
                </div>
              ))}
              <div className="actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => setAdding(false)}
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
                  ? ` · ${Number(product.unit_price).toLocaleString("th-TH", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}`
                  : ` · ${t.dashboard.products.noPrice}`}
              </div>
              {product.description && (
                <div className="card-meta">{product.description}</div>
              )}
              {/* Editing an existing product. The save is an upsert keyed
                  on product_id, so the form already handled edits — it
                  just had no way to be filled with a product's current
                  values, which made it a create-only form in practice. */}
              {permissions.has("product.manage") && (
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
    </AppShell>
  );
}
