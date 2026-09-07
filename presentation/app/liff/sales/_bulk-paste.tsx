"use client";

import { useMemo, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { proxyHeaders } from "./_lib";

type ImportResult = {
  total: number;
  saved: number;
  failed: number;
  rows: { row: number; key: string; status: "saved" | "error"; message: string }[];
};

type Parsed = {
  line: number;
  first_name: string;
  last_name: string;
  phone: string;
  email: string;
  ok: boolean;
};

const EMAIL = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;
const PHONE = /(?:\+66|0)[\d\s\-().]{7,14}/;

/** One pasted line → a customer row. The same reading the chat gives
 * "เพิ่มลูกค้า" with several lines: the email token is the email, the
 * phone-looking run is the phone, what is left is the name (first word is
 * the first name, the rest the last name). Thai digits count as digits. */
export function parseLeadLine(raw: string, line: number): Parsed {
  // Thai digits (U+0E50–U+0E59) count as digits; arithmetic keeps this file free of Thai text.
  const text = raw.replace(/[\u0e50-\u0e59]/g, (d) => String(d.charCodeAt(0) - 0x0e50)).trim();
  const tokens = text.split(/[\s,;]+/).filter(Boolean);
  const email = tokens.find((t) => EMAIL.test(t)) ?? "";
  const phoneMatch = text.match(PHONE);
  const phone = phoneMatch ? phoneMatch[0].replace(/[\s\-().]/g, "").replace(/^\+66/, "0") : "";
  const phoneTokens = new Set(phoneMatch ? phoneMatch[0].split(/\s+/) : []);
  const names = tokens.filter(
    (t) => t !== email && !phoneTokens.has(t) && !/^[\d\-+().]+$/.test(t),
  );
  const ok = names.length > 0 && /^0\d{8,9}$/.test(phone);
  return {
    line,
    first_name: names[0] ?? "",
    last_name: names.slice(1).join(" "),
    phone,
    email,
    ok,
  };
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Rows → the CSV the existing /customers/import route already accepts, so
 * the pasted list and the uploaded file are saved by one code path (same
 * duplicate handling, same 500-row cap, same per-row outcome). */
export function leadsToCsv(rows: Parsed[]): string {
  const head = "first_name,last_name,phone,email";
  const body = rows.map((r) => [r.first_name, r.last_name, r.phone, r.email].map(csvCell).join(","));
  return [head, ...body].join("\n");
}

export function BulkPaste({
  token,
  licenseId,
  onDone,
}: {
  token: string;
  licenseId: string;
  onDone: () => Promise<void> | void;
}) {
  const { t } = useLanguage();
  const copy = t.dashboard.bulkPaste;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);

  const parsed = useMemo(
    () =>
      text
        .split(/\r?\n/)
        .map((line, i) => ({ raw: line, i }))
        .filter(({ raw }) => raw.trim().length > 0)
        .map(({ raw, i }) => parseLeadLine(raw, i + 1)),
    [text],
  );
  const good = parsed.filter((p) => p.ok);
  const bad = parsed.filter((p) => !p.ok);

  async function save() {
    if (good.length === 0) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/customers/import`, {
        method: "POST",
        headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
        body: JSON.stringify({ csv: leadsToCsv(good) }),
      });
      const body = (await response.json().catch(() => null)) as ImportResult | null;
      if (!response.ok || !body || !("rows" in body)) {
        setError(copy.failed);
        return;
      }
      setResult(body);
      // Keep only the lines that were not saved, so the person can fix and
      // resend them instead of retyping everything.
      const savedRows = new Set(body.rows.filter((r) => r.status === "saved").map((r) => r.row));
      const remaining = parsed.filter((p) => {
        const idx = good.indexOf(p);
        return idx === -1 || !savedRows.has(idx + 1);
      });
      setText(remaining.map((p) => [p.first_name, p.last_name, p.phone, p.email].filter(Boolean).join(" ")).join("\n"));
      await onDone();
    } catch {
      setError(copy.failed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>{copy.title}</h2>
      </div>
      <p className="card-meta" style={{ padding: "10px 16px 0" }}>{copy.hint}</p>
      <dl className="fields">
        <div className="field">
          <label htmlFor="bulk-paste">{copy.title}</label>
          <textarea
            id="bulk-paste"
            rows={6}
            value={text}
            placeholder={copy.placeholder}
            onChange={(e) => {
              setText(e.target.value);
              setError("");
            }}
            style={{ font: "inherit", fontSize: 16, width: "100%", boxSizing: "border-box" }}
          />
          {parsed.length > 0 && (
            <span className="hint">
              {copy.ready.replace("{count}", String(good.length))}
              {bad.length > 0 && ` · ${copy.problems.replace("{count}", String(bad.length))}`}
            </span>
          )}
        </div>
        {bad.length > 0 && (
          <ul className="card-meta" style={{ margin: "0 16px", paddingLeft: 18 }}>
            {bad.slice(0, 5).map((p) => (
              <li key={p.line}>{copy.lineNeeds.replace("{line}", String(p.line))}</li>
            ))}
          </ul>
        )}
        <div className="actions">
          <button
            type="button"
            className="btn"
            data-variant="primary"
            disabled={busy || good.length === 0}
            onClick={() => void save()}
          >
            {busy ? copy.saving : `${copy.save} (${good.length})`}
          </button>
          {text && !busy && (
            <button type="button" className="btn" data-variant="quiet" onClick={() => setText("")}>
              {copy.clear}
            </button>
          )}
        </div>
        {error && (
          <p className="status" data-tone="error" role="alert">
            {error}
          </p>
        )}
        {result && (
          <p className="status" data-tone={result.failed ? undefined : "ok"} role="status">
            {copy.summary
              .replace("{saved}", String(result.saved))
              .replace("{failed}", String(result.failed))}
            {result.rows
              .filter((r) => r.status === "error")
              .slice(0, 5)
              .map((r) => (
                <span key={r.row} style={{ display: "block" }}>
                  #{r.row} {r.key ? <code>{r.key}</code> : null}{" "}
                  {r.message.includes("duplicate") ? t.dashboard.csvImport.duplicate : r.message}
                </span>
              ))}
          </p>
        )}
      </dl>
    </section>
  );
}
