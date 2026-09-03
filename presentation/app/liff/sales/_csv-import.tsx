"use client";

import { useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { proxyHeaders } from "./_lib";

type Kind = "products" | "warranties";

type ImportResult = {
  kind: Kind;
  total: number;
  saved: number;
  failed: number;
  rows: { row: number; key: string; status: "saved" | "error"; message: string }[];
};

/**
 * Bulk import from a spreadsheet export (owner, 4 Sep) — the same form
 * for the catalogue and the register of sold units.
 *
 * The sample file is a real download (a static file, no session
 * needed) so the shop can open it in Excel, fill the rows, and bring it
 * back. Every row is applied on its own; the result names each refused
 * row with the reason, next to the button that caused it.
 */
export function CsvImport({
  kind,
  token,
  licenseId,
  onDone,
}: {
  kind: Kind;
  token: string;
  licenseId: string;
  onDone: () => Promise<void> | void;
}) {
  const { t } = useLanguage();
  const copy = t.dashboard.csvImport;
  const input = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);

  async function run() {
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const text = await file.text();
      // Spelled out per kind (not `${kind}/import`) so the boundary check
      // can see both routes exist.
      const url =
        kind === "products"
          ? `/api/phase2/licenses/${licenseId}/products/import`
          : `/api/phase2/licenses/${licenseId}/warranties/import`;
      const response = await fetch(url, {
        method: "POST",
        headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
        body: JSON.stringify({ csv: text }),
      });
      const body = (await response.json().catch(() => null)) as
        | ImportResult
        | { detail?: { error?: string; message?: string } | string }
        | null;
      if (!response.ok || !body || !("rows" in body)) {
        const detail = body && "detail" in body ? body.detail : null;
        const message = typeof detail === "object" && detail?.message ? detail.message : "";
        setError(
          message.includes("missing columns")
            ? copy.missingColumns.replace("{columns}", message.replace("missing columns: ", ""))
            : message.includes("empty") || message.includes("no data")
              ? copy.emptyFile
              : message.includes("more than")
                ? copy.tooMany
                : copy.failed,
        );
        return;
      }
      setResult(body);
      setFile(null);
      if (input.current) input.current.value = "";
      await onDone();
    } catch {
      setError(copy.failed);
    } finally {
      setBusy(false);
    }
  }

  const fieldId = `csv-${kind}`;
  return (
    <section className="section">
      <div className="section-head">
        <h2>{kind === "products" ? copy.titleProducts : copy.titleWarranties}</h2>
        <a className="btn" data-variant="quiet" href={`/samples/${kind}.csv`} download>
          {copy.sample}
        </a>
      </div>
      <p className="card-meta" style={{ padding: "10px 16px 0" }}>
        {kind === "products" ? copy.hintProducts : copy.hintWarranties}
      </p>
      <dl className="fields">
        <div className="field">
          <label htmlFor={fieldId}>{copy.file}</label>
          <input
            id={fieldId}
            ref={input}
            type="file"
            accept=".csv,text/csv"
            aria-describedby={`${fieldId}-hint`}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError("");
            }}
          />
          <span id={`${fieldId}-hint`} className="hint">{copy.fileHint}</span>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn"
            data-variant="primary"
            disabled={busy || !file}
            onClick={() => void run()}
          >
            {busy ? copy.importing : copy.import}
          </button>
          {file && !busy && <span className="card-meta">{file.name}</span>}
        </div>
        {error && (
          <p className="status" data-tone="error" role="alert">
            {error}
          </p>
        )}
      </dl>
      {result && (
        <div style={{ padding: "0 16px 16px" }}>
          <p className="status" data-tone={result.failed ? undefined : "ok"} role="status">
            {copy.summary
              .replace("{saved}", String(result.saved))
              .replace("{failed}", String(result.failed))
              .replace("{total}", String(result.total))}
          </p>
          {result.failed > 0 && (
            <div className="tablewrap" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>{copy.colRow}</th>
                    <th>{copy.colKey}</th>
                    <th>{copy.colReason}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows
                    .filter((r) => r.status === "error")
                    .map((r) => (
                      <tr key={r.row}>
                        <td>{r.row}</td>
                        <td>
                          <code>{r.key || "—"}</code>
                        </td>
                        <td>
                          {r.message.includes("duplicate")
                            ? copy.duplicate
                            : r.message.includes("required")
                              ? copy.required
                              : r.message.includes("bad date")
                                ? copy.badDate
                                : r.message}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
