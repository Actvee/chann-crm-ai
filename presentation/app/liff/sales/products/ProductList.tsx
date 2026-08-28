"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell, CompanyPicker, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Membership, initLiffSession, proxyHeaders } from "../_lib";

type Product = {
  id: string;
  sku?: string | null;
  name?: string | null;
  unit_price?: string | number | null;
};

export default function ProductList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
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
      if (!session.memberships.length) say(t.liff.noCompany, "error");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say]);

  const needle = query.trim().toLowerCase();
  const visible = needle
    ? products.filter((product) =>
        [product.name, product.sku]
          .map((value) => String(value ?? "").toLowerCase())
          .some((value) => value.includes(needle)),
      )
    : products;

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
              <div className="card-title">{product.name ?? "—"}</div>
              <div className="card-meta">
                <span className="code">{product.sku ?? t.dashboard.products.noSku}</span>
                {product.unit_price != null && product.unit_price !== ""
                  ? ` · ${Number(product.unit_price).toLocaleString("th-TH", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}`
                  : ` · ${t.dashboard.products.noPrice}`}
              </div>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
