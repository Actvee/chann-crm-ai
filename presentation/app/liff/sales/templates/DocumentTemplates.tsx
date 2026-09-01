"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../_components";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

type TemplateVersion = {
  id: string;
  version: number;
  status: string;
};

type Template = {
  id: string;
  template_name: string;
  template_code: string;
  document_type: string;
  is_active: boolean;
};

/**
 * A shop's own quote layout.
 *
 * The hard part of this page is not the upload, it is explaining what
 * can go in the file. Placeholders are the entire language — there are
 * no conditions, no loops beyond the line-item block — so the reference
 * has to be right there rather than in documentation nobody opens.
 *
 * Uploads land as drafts. A template goes onto documents customers
 * receive, and the person who wrote it should see it rendered before
 * anyone else does.
 */
export default function DocumentTemplates({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const [licenseId, setLicenseId] = useState("");
  const [token, setToken] = useState("");
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [templates, setTemplates] = useState<Template[]>([]);
  const [versions, setVersions] = useState<Record<string, TemplateVersion[]>>({});
  const [name, setName] = useState("");
  const [html, setHtml] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      const headers = proxyHeaders(currentToken, license);
      const response = await fetch(
        `/api/phase2/licenses/${license}/document-templates?document_type=quote`,
        { headers },
      );
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      const rows = (await response.json()) as Template[];
      // The built-in layout is not a template anyone uploaded and cannot
      // be edited here; listing it would invite someone to try.
      setTemplates(rows.filter((row) => !row.template_code.startsWith("builtin")));
      say("");
    },
    [licenseId, say, t, token],
  );

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      setToken(session.token);
      setLicenseId(license);
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setPermissions(await fetchPermissions(session.token, license));
      await load(session.token, license);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  async function upload() {
    if (!name.trim() || !html.trim()) {
      say(t.dashboard.templates.needsNameAndFile, "error");
      return;
    }
    setBusy(true);
    setWarnings([]);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/document-templates/upload`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            template_name: name.trim(),
            html,
            document_type: "quote",
          }),
        },
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        say(
          typeof detail.detail === "string"
            ? detail.detail
            : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      const result = (await response.json()) as {
        unknown_placeholders?: string[];
      };
      // Reported, not blocking. A placeholder that resolves to nothing
      // may be deliberate — but finding out here beats finding out from
      // a customer holding a document with a gap in it.
      setWarnings(result.unknown_placeholders ?? []);
      setHtml("");
      setName("");
      await load();
      say(t.dashboard.templates.uploaded, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function loadVersions(template: Template) {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/document-templates/${template.id}/versions`,
      { headers: proxyHeaders(token, licenseId) },
    );
    if (!response.ok) return;
    setVersions({
      ...versions,
      [template.id]: (await response.json()) as TemplateVersion[],
    });
  }

  async function publish(template: Template, version: TemplateVersion) {
    if (!window.confirm(t.dashboard.templates.confirmPublish)) return;
    setBusy(true);
    try {
      const response = await fetch(
        // One string, not two concatenated: a split URL reads as two
        // different paths and hides typos in the join.
        `/api/phase2/licenses/${licenseId}/document-templates/${template.id}/versions/${version.id}/publish`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      await loadVersions(template);
      say(t.dashboard.templates.published, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function readFile(file: File) {
    setHtml(await file.text());
    if (!name.trim()) setName(file.name.replace(/\.html?$/i, ""));
  }

  const canManage = permissions.has("setting.manage");

  return (
    <AppShell
      title={t.dashboard.templates.title}
      back="/liff/sales"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p style={{ color: "var(--ink-soft)", fontSize: 14.5, margin: "0 0 16px" }}>
        {t.dashboard.templates.intro}
      </p>

      {canManage && (
        <section className="section" style={{ marginBottom: 16 }}>
          <div className="section-head">
            <h2>{t.dashboard.templates.upload}</h2>
          </div>
          <dl className="fields">
            <div className="field-row">
              <dt>{t.dashboard.templates.name}</dt>
              <dd>
                <input
                  value={name}
                  placeholder={t.dashboard.templates.namePlaceholder}
                  onChange={(event) => setName(event.target.value)}
                />
              </dd>
            </div>
            <div className="field-row">
              <dt>{t.dashboard.templates.file}</dt>
              <dd>
                <input
                  type="file"
                  accept=".html,.htm,text/html"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void readFile(file);
                  }}
                />
              </dd>
            </div>
            {html && (
              <div className="field-row">
                <dt>{t.dashboard.templates.loaded}</dt>
                <dd>{t.dashboard.templates.characters.replace("{n}", String(html.length))}</dd>
              </div>
            )}
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => void upload()}
                disabled={busy || !html}
              >
                {busy ? t.dashboard.saving : t.dashboard.templates.upload}
              </button>
            </div>
          </dl>
        </section>
      )}

      {warnings.length > 0 && (
        <div className="info-note">
          <p>{t.dashboard.templates.blankWarning}</p>
          <ul>
            {warnings.map((placeholder) => (
              <li key={placeholder}>
                <code>{`{{${placeholder}}}`}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The reference, on the page. Placeholders are the entire language
          and there is nowhere else someone would think to look. */}
      <details className="section" style={{ padding: "14px 16px", marginBottom: 16 }}>
        <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 14 }}>
          {t.dashboard.templates.reference}
        </summary>
        <pre
          style={{
            fontFamily: "var(--font-data)",
            fontSize: 12.5,
            lineHeight: 1.7,
            whiteSpace: "pre-wrap",
            margin: "12px 0 0",
            color: "var(--ink-soft)",
          }}
        >{`{{company.legal_name}}   ชื่อบริษัท
{{company.address}}      ที่อยู่
{{company.phone}}        เบอร์โทร
{{company.tax_id}}       เลขประจำตัวผู้เสียภาษี

{{customer.name}}        ชื่อลูกค้า
{{customer.address}}     ที่อยู่ลูกค้า

{{quote.quote_id}}       เลขที่ใบเสนอราคา
{{quote.valid_until}}    วันหมดอายุ

{{#line_items}}
  {{item.index}}         ลำดับ
  {{item.name}}          ชื่อสินค้า
  {{item.qty}}           จำนวน
  {{item.unit_price}}    ราคาต่อหน่วย
  {{item.line_total}}    รวมบรรทัด
{{/line_items}}

{{totals.subtotal}}      รวมเป็นเงิน
{{totals.discount_amount}} ส่วนลด
{{totals.vat_amount}}    ภาษีมูลค่าเพิ่ม
{{totals.grand_total}}   จำนวนเงินรวมทั้งสิ้น`}</pre>
      </details>

      {templates.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.templates.empty}</p>
        </div>
      ) : (
        <ul className="list">
          {templates.map((template) => (
            <li key={template.id} className="card">
              <div className="card-title">{template.template_name}</div>
              <div className="card-actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => void loadVersions(template)}
                >
                  {t.dashboard.templates.versions}
                </button>
              </div>
              {versions[template.id]?.map((version) => (
                <div key={version.id} className="card-meta">
                  v{version.version} · {version.status}
                  {canManage && version.status !== "published" && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="quiet"
                      style={{ marginLeft: 8 }}
                      onClick={() => void publish(template, version)}
                      disabled={busy}
                    >
                      {t.dashboard.templates.publish}
                    </button>
                  )}
                </div>
              ))}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
