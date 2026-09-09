"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { FieldRow } from "../../_field-row";
import { openExternal } from "../../_shared";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";

type TemplateVersion = {
  id: string;
  version: number;
  status: string;
  /** A link back to the Word file this version was made from, when it
   *  came from one. Null for an HTML upload and for the built-in — the
   *  server sends null rather than a link that 4xxs, so the button
   *  below is simply absent. */
  source_docx_url?: string | null;
};

type Preview = {
  versionId: string;
  version: number;
  html: string;
  blanks: string[];
};

/** Word's own media type, plus the extensions, because a phone's file
 *  picker reports one or the other and rarely both. */
const DOCX_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const MAX_UPLOAD_BYTES = 2_000_000;

/** The kinds of document that actually have an issue path behind them.
 *  Mirrors TEMPLATE_DOCUMENT_TYPES in the Application tier: offering a
 *  type nothing renders would let a shop maintain a file for nothing. */
const DOCUMENT_TYPES = ["quote", "service_report"] as const;
type DocumentType = (typeof DOCUMENT_TYPES)[number];

type Template = {
  id: string;
  template_name: string;
  template_code: string;
  document_type: string;
  is_active: boolean;
};

/** Which layout each kind of document is rendered from right now, as the
 *  renderer itself resolves it (GET document-templates/in-use). */
type InUse = {
  source: string;
  template_id: string | null;
  template_name: string | null;
  version_id: string | null;
  version: number | null;
};

const NO_TEMPLATES: Template[] = [];

/**
 * A shop's own document layouts.
 *
 * Three questions this page has to answer, in order: what can go in the
 * file, which kind of document is this template for, and which one are
 * my customers actually getting.
 *
 * The first is the placeholder reference, on the page rather than in
 * documentation nobody opens — placeholders are the entire language.
 *
 * The second used to have no answer: every upload was filed as a quote
 * template no matter what was in it, so a service-report layout could
 * not be created from here at all even though the report issue path
 * looks for one. There is a type chooser now, and the list is grouped by
 * type.
 *
 * The third had no answer either. A shop with two published quote
 * templates got whichever came first out of the list. Each type's
 * section now names the template in use and offers the switch.
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
  const [templates, setTemplates] = useState<Record<string, Template[]>>({});
  const [inUse, setInUse] = useState<Record<string, InUse>>({});
  const [versions, setVersions] = useState<Record<string, TemplateVersion[]>>({});
  const [name, setName] = useState("");
  const [documentType, setDocumentType] = useState<DocumentType>("quote");
  const [html, setHtml] = useState("");
  // A Word upload travels as base64 through the one JSON seam this tier
  // has (lib/api.ts); `docxName` is kept so the server can tell a .doc
  // from a .docx and name the stored original.
  const [docx, setDocx] = useState("");
  const [docxName, setDocxName] = useState("");
  const [docxBytes, setDocxBytes] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const typeLabel = useCallback(
    (type: string) =>
      (t.dashboard.templates.documentTypes as Record<string, string>)[type] ?? type,
    [t],
  );

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      const headers = proxyHeaders(currentToken, license);
      // Every type in one pass, plus the renderer's own answer to "which
      // one is in use". Loading only quotes is what hid the whole
      // service-report half of this page.
      const responses = await Promise.all([
        ...DOCUMENT_TYPES.map((type) =>
          fetch(
            `/api/phase2/licenses/${license}/document-templates?document_type=${type}`,
            { headers },
          ),
        ),
        fetch(`/api/phase2/licenses/${license}/document-templates/in-use`, {
          headers,
        }),
      ]);
      const failed = responses.find((response) => !response.ok);
      if (failed) {
        throw new Error(
          failed.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${failed.status})`,
        );
      }
      const byType: Record<string, Template[]> = {};
      for (let index = 0; index < DOCUMENT_TYPES.length; index += 1) {
        const rows = (await responses[index].json()) as Template[];
        // The built-in layout is not a template anyone uploaded and
        // cannot be edited here; listing it would invite someone to try.
        // Case-insensitively, because the codes are BUILTIN-QUOTE and
        // BUILTIN-SERVICE-REPORT and the old lowercase test matched
        // neither — the built-in has been on this list all along.
        byType[DOCUMENT_TYPES[index]] = rows.filter(
          (row) => !row.template_code.toLowerCase().startsWith("builtin"),
        );
      }
      setTemplates(byType);
      setInUse((await responses[DOCUMENT_TYPES.length].json()) as Record<string, InUse>);
      say("");
    },
    [licenseId, say, t, token],
  );

  // The shared session (review C4/C5): the shop, its permissions and the
  // suspended notice come from one place, and a switch starts over.
  const session = useSalesSession(liffId, say);
  useEffect(() => {
    if (!session.ready) return;
    setToken(session.token);
    setLicenseId(session.licenseId);
    setPermissions(session.permissions);
    load(session.token, session.licenseId).catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error"),
    );
  }, [session.ready, session.token, session.licenseId, session.permissions, load, say, t]);

  async function upload() {
    if (!name.trim() || (!html.trim() && !docx)) {
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
          // One field or the other, never both: the server converts a
          // .docx itself and keeps the original bytes, so sending HTML
          // alongside it would mean two answers to "what did they
          // upload" and no way to tell which one rendered.
          body: JSON.stringify(
            docx
              ? {
                  template_name: name.trim(),
                  docx_base64: docx,
                  filename: docxName,
                  document_type: documentType,
                }
              : {
                  template_name: name.trim(),
                  html,
                  document_type: documentType,
                },
          ),
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
      setDocx("");
      setDocxName("");
      setDocxBytes(0);
      setName("");
      await load();
      say(t.dashboard.templates.uploaded, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function loadVersions(template: Template) {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/document-templates/${template.id}/versions`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        // Silence here made the button look dead; a failure is a fact
        // worth a sentence.
        say(
          t.dashboard.templates.versionsFailed.replace("{status}", String(response.status)),
          "error",
        );
        return;
      }
      setVersions({
        ...versions,
        [template.id]: (await response.json()) as TemplateVersion[],
      });
    } finally {
      setBusy(false);
    }
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
      // Publishing can change which layout is in use — reload the answer
      // rather than leaving the page asserting the old one.
      await load();
      say(t.dashboard.templates.published, "ok");
    } finally {
      setBusy(false);
    }
  }

  /** Choose the layout a kind of document is rendered from, or hand the
   *  type back to the system's standard one.
   *
   *  Nothing already issued changes: a generated document names the
   *  version that rendered it, which is the whole point of recording it. */
  async function chooseTemplate(template: Template, active: boolean) {
    if (
      !window.confirm(
        (active
          ? t.dashboard.templates.confirmChoose
          : t.dashboard.templates.confirmUseBuiltin
        ).replace("{name}", template.template_name),
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/document-templates/${template.id}/active`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({ is_active: active }),
        },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      await load();
      say(
        active ? t.dashboard.templates.chosen : t.dashboard.templates.builtinChosen,
        "ok",
      );
    } finally {
      setBusy(false);
    }
  }

  /** Whatever the shop picked, in the shape the upload wants.
   *
   *  A .docx is binary: read as bytes and base64'd here rather than
   *  `file.text()`, which returns mojibake for a zip and would upload a
   *  file the server cannot open. Chunked, because spreading a two
   *  megabyte Uint8Array into String.fromCharCode overflows the stack. */
  async function readFile(file: File) {
    const lower = file.name.toLowerCase();
    const isDocx = lower.endsWith(".docx") || file.type === DOCX_TYPE;
    const isHtml = lower.endsWith(".html") || lower.endsWith(".htm");
    if (!isDocx && !isHtml) {
      // Named first, because ".doc" is the common case and "unsupported
      // file" does not tell anyone to save it as .docx.
      say(t.dashboard.templates.wrongType, "error");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      say(t.dashboard.templates.tooBig, "error");
      return;
    }
    try {
      if (isDocx) {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        for (let i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        }
        setDocx(btoa(binary));
        setDocxName(file.name);
        setDocxBytes(file.size);
        setHtml("");
      } else {
        setHtml(await file.text());
        setDocx("");
        setDocxName("");
        setDocxBytes(0);
      }
    } catch {
      say(t.dashboard.templates.readFailed, "error");
      return;
    }
    if (!name.trim()) setName(file.name.replace(/\.(docx|html?)$/i, ""));
    say("");
  }

  /** Look at a version rendered, before a customer does.
   *
   *  The reply is JSON with the filled HTML in it rather than an HTML
   *  response, because every call this page makes goes through the
   *  proxy that parses JSON — and because the blank-placeholder list
   *  comes back in the same round trip, which is the other half of the
   *  question "is this template right". */
  async function openPreview(template: Template, version: TemplateVersion) {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/document-templates/${template.id}/versions/${version.id}/preview`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        say(
          typeof detail.detail === "string"
            ? detail.detail
            : t.dashboard.templates.previewFailed.replace(
                "{status}",
                String(response.status),
              ),
          "error",
        );
        return;
      }
      const result = (await response.json()) as {
        html: string;
        unknown_placeholders?: string[];
      };
      setPreview({
        versionId: version.id,
        version: version.version,
        html: result.html,
        blanks: result.unknown_placeholders ?? [],
      });
      // Previewing moves a draft to "previewed" — reload so the row says
      // so rather than looking as though nothing happened.
      await loadVersions(template);
      say("");
    } finally {
      setBusy(false);
    }
  }

  /** The preview in the phone's own browser, for printing or sharing.
   *  A blob URL, because the HTML is already in hand and the preview
   *  route needs a session header a plain navigation cannot send. */
  function openPreviewExternally() {
    if (!preview) return;
    const url = URL.createObjectURL(
      new Blob([preview.html], { type: "text/html;charset=utf-8" }),
    );
    openExternal(url);
    // Long enough for the new tab to have fetched it; revoking
    // immediately races the open and shows a blank page.
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  const canManage = !session.suspended && permissions.has("setting.manage");

  return (
    <SalesShell
      session={session}
      title={t.dashboard.templates.title}
      back="/liff/sales"
      liffId={liffId}
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
            {/* Which document this layout is for. Asked before the file,
                because it decides which placeholder set applies and
                which sample to start from — and because everything
                uploaded here used to be filed as a quote. */}
            <FieldRow label={t.dashboard.templates.documentType}>
              {(id) => (
                <select
                  id={id}
                  value={documentType}
                  onChange={(event) =>
                    setDocumentType(event.target.value as DocumentType)
                  }
                >
                  {DOCUMENT_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {typeLabel(type)}
                    </option>
                  ))}
                </select>
              )}
            </FieldRow>
            <FieldRow label={t.dashboard.templates.name}>
              {(id) => (
                <input
                  id={id}
                  value={name}
                  placeholder={t.dashboard.templates.namePlaceholder}
                  onChange={(event) => setName(event.target.value)}
                />
              )}
            </FieldRow>
            <FieldRow label={t.dashboard.templates.file}>
              {(id) => (
                <input
                  id={id}
                  type="file"
                  // .doc is deliberately absent: the picker offering it
                  // and the server refusing it is worse than the picker
                  // not offering it. readFile says what to do instead
                  // when someone types the name in anyway.
                  accept={`.docx,${DOCX_TYPE},.html,.htm,text/html`}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void readFile(file);
                  }}
                />
              )}
            </FieldRow>
            <div className="field-row">
              <dt />
              <dd style={{ color: "var(--ink-soft)", fontSize: 13 }}>
                {t.dashboard.templates.fileHint}
              </dd>
            </div>
            {docx && (
              <div className="field-row">
                <dt>{t.dashboard.templates.loadedDocx}</dt>
                <dd>
                  {docxName}{" "}
                  {t.dashboard.templates.kilobytes.replace(
                    "{n}",
                    String(Math.max(1, Math.round(docxBytes / 1024))),
                  )}
                </dd>
              </div>
            )}
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
                disabled={busy || (!html && !docx)}
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

      {/* Something to start from. The owner, 9 Sep: "ไม่มีตัวอย่างที่เป็น
          ไฟล์ให้ดาวน์โหลดไปดู" — the placeholder reference below tells
          you the vocabulary but not what a document made of it looks
          like, and a shop should not have to build one from an empty
          page to find out. openExternal, not <a download>: the LINE
          in-app browser does nothing with a download link. */}
      <section className="section" style={{ padding: "14px 16px", marginBottom: 16 }}>
        <div className="section-head">
          <h2>{t.dashboard.templates.samplesTitle}</h2>
        </div>
        <p style={{ color: "var(--ink-soft)", fontSize: 13.5, margin: "0 0 10px" }}>
          {t.dashboard.templates.samplesHint}
        </p>
        <div className="actions">
          <button
            type="button"
            className="btn"
            data-variant="quiet"
            onClick={() =>
              openExternal(`${window.location.origin}/api/template-sample/quote`)
            }
          >
            {t.dashboard.templates.sampleQuote}
          </button>
          <button
            type="button"
            className="btn"
            data-variant="quiet"
            onClick={() =>
              openExternal(
                `${window.location.origin}/api/template-sample/service_report`,
              )
            }
          >
            {t.dashboard.templates.sampleServiceReport}
          </button>
        </div>
      </section>

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
        >{t.dashboard.templates.placeholderLegend}</pre>
      </details>

      {/* One section per kind of document, each headed by the layout its
          documents are actually being rendered from. Grouping is not
          decoration: a quote template and a service-report template
          share nothing but the upload box, and a flat list gave no way
          to tell which was which. */}
      {DOCUMENT_TYPES.map((type) => {
        const rows = templates[type] ?? NO_TEMPLATES;
        const current = inUse[type];
        return (
          <section key={type} className="section" style={{ marginBottom: 16 }}>
            <div className="section-head">
              <h2>{typeLabel(type)}</h2>
            </div>
            <p style={{ color: "var(--ink-soft)", fontSize: 13.5, margin: "0 0 10px" }}>
              {t.dashboard.templates.inUse}{" "}
              <strong style={{ color: "var(--ink)" }}>
                {current && current.source === "tenant"
                  ? `${current.template_name ?? ""} (${t.dashboard.templates.versionNumber.replace(
                      "{n}",
                      String(current.version ?? ""),
                    )})`
                  : t.dashboard.templates.builtinLayout}
              </strong>
            </p>

            {rows.length === 0 ? (
              <p style={{ color: "var(--ink-soft)", fontSize: 13.5, margin: 0 }}>
                {t.dashboard.templates.emptyForType}
              </p>
            ) : (
              <ul className="list">
                {rows.map((template) => {
                  const isUsed = current?.template_id === template.id;
                  return (
                  <li key={template.id} className="card">
                    <div className="card-title">
                      {template.template_name}{" "}
                      {isUsed && (
                        <span className="badge" data-stage="won">
                          {t.dashboard.templates.usedNow}
                        </span>
                      )}
                    </div>
                    <div className="card-actions">
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        onClick={() => void loadVersions(template)}
                        disabled={busy}
                      >
                        {t.dashboard.templates.versions}
                      </button>
                      {/* The choice itself. `is_active` is what the
                          renderer reads; before this there was no way to
                          write it, so two published templates meant a
                          coin toss. The button follows what is IN USE
                          rather than the flag: a shop that never chose
                          has every template flagged active, and asking
                          them to switch one off before switching another
                          on would be a puzzle, not a choice. */}
                      {canManage && !isUsed && (
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          onClick={() => void chooseTemplate(template, true)}
                          disabled={busy}
                        >
                          {t.dashboard.templates.chooseThis}
                        </button>
                      )}
                      {canManage && isUsed && (
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          onClick={() => void chooseTemplate(template, false)}
                          disabled={busy}
                        >
                          {t.dashboard.templates.useBuiltin}
                        </button>
                      )}
                    </div>
                    {versions[template.id]?.map((version) => (
                      <div key={version.id} className="card-meta">
                        v{version.version} ·{" "}
                        {(t.dashboard.templates.versionStatus as Record<string, string>)[
                          version.status
                        ] ?? "—"}
                        {/* On every row, published ones included: "what
                            does the layout my customers are getting look
                            like" is the question this page could not
                            answer at all. */}
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          style={{ marginLeft: 8 }}
                          onClick={() => void openPreview(template, version)}
                          disabled={busy}
                        >
                          {t.dashboard.templates.preview}
                        </button>
                        {version.source_docx_url && (
                          <button
                            type="button"
                            className="btn"
                            data-variant="quiet"
                            style={{ marginLeft: 8 }}
                            onClick={() => openExternal(version.source_docx_url!)}
                          >
                            {t.dashboard.templates.sourceDownload}
                          </button>
                        )}
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
                        {preview?.versionId === version.id && (
                          <div style={{ marginTop: 10 }}>
                            <div
                              style={{
                                display: "flex",
                                gap: 8,
                                alignItems: "center",
                                flexWrap: "wrap",
                                marginBottom: 6,
                              }}
                            >
                              <strong style={{ fontSize: 13 }}>
                                {t.dashboard.templates.previewTitle}
                              </strong>
                              <button
                                type="button"
                                className="btn"
                                data-variant="quiet"
                                onClick={openPreviewExternally}
                              >
                                {t.dashboard.templates.previewOpen}
                              </button>
                              <button
                                type="button"
                                className="btn"
                                data-variant="quiet"
                                onClick={() => setPreview(null)}
                              >
                                {t.dashboard.templates.previewClose}
                              </button>
                            </div>
                            <p
                              style={{
                                color: "var(--ink-soft)",
                                fontSize: 12.5,
                                margin: "0 0 8px",
                              }}
                            >
                              {t.dashboard.templates.previewNote}
                            </p>
                            {preview.blanks.length > 0 && (
                              <div className="info-note">
                                <p>{t.dashboard.templates.blankWarning}</p>
                                <ul>
                                  {preview.blanks.map((placeholder) => (
                                    <li key={placeholder}>
                                      <code>{`{{${placeholder}}}`}</code>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {/* sandbox with nothing granted: this is a
                                tenant's own file rendered on our origin,
                                and the preview must not be able to
                                script, submit a form, or navigate the
                                page it sits in. */}
                            <iframe
                              title={t.dashboard.templates.previewTitle}
                              sandbox=""
                              srcDoc={preview.html}
                              style={{
                                width: "100%",
                                height: 460,
                                border: "1px solid var(--line)",
                                borderRadius: 8,
                                background: "#fff",
                              }}
                            />
                          </div>
                        )}
                      </div>
                    ))}
                  </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </SalesShell>
  );
}
