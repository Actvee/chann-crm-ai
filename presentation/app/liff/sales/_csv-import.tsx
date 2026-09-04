"use client";

import { useEffect, useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { openExternal, proxyHeaders } from "./_lib";

type Kind = "products" | "warranties" | "customers";

type ImportResult = {
  kind: Kind;
  total: number;
  saved: number;
  failed: number;
  rows: { row: number; key: string; status: "saved" | "error"; message: string }[];
};

/** A small CSV reader for the sample preview — quoted fields, commas. */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') {
        quoted = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else if (ch !== "\r") {
      cell += ch;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim()));
}

/**
 * Bulk import from a spreadsheet export (owner, 4 Sep) — the same form
 * for the catalogue and the register of sold units.
 *
 * The sample is shown right here as a table (the LINE in-app browser
 * often does nothing on a download link), can be copied to paste into a
 * spreadsheet, or opened in the phone's browser. Every row is applied on
 * its own; the result names each refused row with the reason, next to
 * the button that caused it.
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
  const [sample, setSample] = useState<string>("");
  const [copied, setCopied] = useState(false);
  const samplePath = `/samples/${kind}.csv`;

  useEffect(() => {
    let alive = true;
    fetch(samplePath)
      .then((r) => (r.ok ? r.text() : ""))
      .then((text) => {
        if (alive) setSample(text);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [samplePath]);

  async function copySample() {
    try {
      await navigator.clipboard.writeText(sample);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch {
      setCopied(false);
    }
  }

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
          : kind === "customers"
            ? `/api/phase2/licenses/${licenseId}/customers/import`
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
  const sampleRows = sample ? parseCsv(sample) : [];
  return (
    <section className="section">
      <div className="section-head">
        <h2>{kind === "products" ? copy.titleProducts : kind === "customers" ? copy.titleCustomers : copy.titleWarranties}</h2>
      </div>
      <p className="card-meta" style={{ padding: "10px 16px 0" }}>
        {kind === "products" ? copy.hintProducts : kind === "customers" ? copy.hintCustomers : copy.hintWarranties}
      </p>

      {sampleRows.length > 0 && (
        <div style={{ padding: "10px 16px 0" }}>
          <p className="card-meta" style={{ margin: "0 0 6px", fontWeight: 600 }}>{copy.sampleTitle}</p>
          <div className="tablewrap" style={{ overflowX: "auto" }}>
            <table className="table sample-table">
              <thead>
                <tr>
                  {sampleRows[0].map((h, i) => (
                    <th key={i}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sampleRows.slice(1).map((r, ri) => (
                  <tr key={ri}>
                    {r.map((c, ci) => (
                      <td key={ci}>{c || "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="actions" style={{ marginTop: 8 }}>
            <button type="button" className="btn" onClick={() => void copySample()}>
              {copied ? copy.copied : copy.copySample}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="quiet"
              onClick={() => openExternal(`${window.location.origin}${samplePath}`)}
            >
              {copy.openSample}
            </button>
          </div>
        </div>
      )}

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
