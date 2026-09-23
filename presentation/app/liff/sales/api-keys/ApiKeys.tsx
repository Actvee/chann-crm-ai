"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { ConfirmDialog, useConfirm } from "../../_confirm";
import { Sheet } from "../../_sheet";
import { shortDate } from "../../_list-controls";
import { Empty } from "../_components";
import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";

type ApiKey = {
  id: string; name: string; key_prefix: string; created_at: string;
  last_used_at: string | null; revoked_at: string | null;
};

/**
 * Round 21B — the owner's outside access.
 *
 * One list, one primary action (ui-ux-pro-max: one primary CTA per
 * screen). The key is shown exactly once, in the sheet that made it,
 * with a copy control and the sentence that says so; after "ปิด" only
 * the prefix survives. Revoking is a separate, red, confirmed action,
 * set apart under a rule rather than beside the row it belongs to
 * (ui-ux-pro-max: destructive actions visually separated,
 * confirmation-dialogs before an irreversible action). Owner only: the
 * rail hides the entry for anyone else and the route answers 403
 * owner_only.
 *
 * `RecordActions` (_record.tsx) requires a `title` heading per instance,
 * which is right for a single record's detail page but would print the
 * same "API สำหรับระบบภายนอก" heading on every row of a list — so this
 * follows the row-action pattern InvoiceList.tsx already uses instead:
 * plain buttons, the danger one under `.record-danger`'s rule.
 */
export default function ApiKeys({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const c = t.dashboard.apiKeys;
  const failureText = useFailureText();
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [docsUrl, setDocsUrl] = useState<string | null>(null);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, isOwner } = session;

  // `proxyHeaders` returns a new object every call, so a `headers` value
  // built inline and put in `load`'s deps gave `load` a new identity on
  // every render — the mount effect below depends on `load`, so it fired
  // again after every setKeys/setDocsUrl and never stopped (round 21B fix
  // 1). Depending on `token`/`licenseId` (primitives) instead, and
  // building headers inside the callback the way DealDetail.tsx does,
  // makes `load` stable across renders.
  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const headers = proxyHeaders(token, licenseId);
    const res = await fetch(`/api/phase2/licenses/${licenseId}/api-keys`, { headers });
    if (!res.ok) throw new Error(res.status === 403 ? c.ownerOnly : `${t.dashboard.loadFailed} (${res.status})`);
    const body = (await res.json()) as { keys: ApiKey[]; docs_url: string | null };
    setKeys(body.keys);
    setDocsUrl(body.docs_url);
  }, [token, licenseId, c.ownerOnly, t.dashboard.loadFailed]);

  useEffect(() => {
    if (!session.ready) return;
    load().then(() => say(""), (e: Error) => say(e.message, "error"));
  }, [session.ready, load, say]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!token || !licenseId || !name.trim()) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/phase2/licenses/${licenseId}/api-keys`, {
        method: "POST", headers: proxyHeaders(token, licenseId), body: JSON.stringify({ name: name.trim() }),
      });
      if (!res.ok) throw new Error(await failureText(res));
      const made = (await res.json()) as ApiKey & { key: string };
      setFresh({ name: made.name, key: made.key });
      setName("");
      setCopied(false);
      await load();
      say(c.created, "ok");
    } catch (e) {
      say((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function copyKey() {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(fresh.key);
      setCopied(true);
    } catch {
      // Clipboard access can be denied (permissions, non-HTTPS, an old
      // in-app browser) — say so rather than leaving "คัดลอก" looking
      // like it did nothing. The value stays selectable in the box
      // (`user-select: all` on .api-key-value) as the fallback.
      setCopied(false);
      say(c.copyFailed, "error");
    }
  }

  // useConfirm().ask takes a ConfirmRequest (action/target/affects/…) and
  // resolves a boolean, rather than the {title,message,onConfirm} shape
  // the brief sketched — the caller does the work itself after a true,
  // the same pattern SalesTeams.tsx's deleteTeam/removeMember use.
  async function askRevoke(row: ApiKey) {
    if (revokingId) return; // one revoke in flight at a time
    const ok = await ask({
      action: c.revoke,
      target: row.name,
      // The dialog composes its own "{action} {target}" title, so only
      // the consequence sentence belongs in `affects` — its own i18n key
      // (`revokeAffects`) rather than a regex slice of a whole question
      // (round 21B fix 4).
      affects: [c.revokeAffects],
      permanent: true,
      confirmLabel: c.revoke,
    });
    if (!ok || !token || !licenseId) return;
    setRevokingId(row.id);
    try {
      const res = await fetch(`/api/phase2/licenses/${licenseId}/api-keys/${row.id}/revoke`, {
        method: "POST", headers: proxyHeaders(token, licenseId),
      });
      if (!res.ok) {
        say(await failureText(res), "error");
        return;
      }
      await load();
      say(c.revoked, "ok");
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <SalesShell session={session} title={c.title} back="/liff/sales" liffId={liffId}
      status={status} statusTone={tone} onSdkError={() => say(t.liff.sdkLoadFailed, "error")}>
      <p className="page-intro">{c.intro}</p>
      {docsUrl && (
        <p className="card-meta"><a href={docsUrl} target="_blank" rel="noreferrer">{c.docs}</a></p>
      )}
      {session.ready && !isOwner ? (
        <Empty message={c.ownerOnly} />
      ) : (
        <>
          <div className="list-head">
            <span className="count">{keys.length}</span>
            <div className="list-tools">
              <button type="button" className="btn" data-variant="primary" onClick={() => setCreating(true)} disabled={busy}>
                {c.create}
              </button>
            </div>
          </div>
          {keys.length === 0 ? (
            <Empty message={c.empty} />
          ) : (
            <ul className="list">
              {keys.map((row) => (
                <li key={row.id} className="card">
                  <div className="card-title">
                    {row.name}
                    <span className="code" style={{ marginLeft: 8 }}>{row.key_prefix}…</span>
                  </div>
                  <p className="card-meta">
                    {c.createdOn.replace("{when}", shortDate(row.created_at, locale))} ·{" "}
                    {row.last_used_at ? c.lastUsed.replace("{when}", shortDate(row.last_used_at, locale)) : c.neverUsed}
                  </p>
                  <div className="actions record-danger">
                    <button
                      type="button"
                      className="btn"
                      data-variant="danger"
                      onClick={() => void askRevoke(row)}
                      disabled={busy || revokingId !== null}
                      aria-busy={revokingId === row.id || undefined}
                    >
                      {c.revoke}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <Sheet open={creating} title={fresh ? c.created : c.create} onClose={() => { setCreating(false); setFresh(null); }}>
        {fresh ? (
          <div className="api-key-reveal">
            <p className="card-meta">{fresh.name}</p>
            <output className="api-key-value" aria-live="polite">{fresh.key}</output>
            <p className="api-key-once">{c.keyOnce}</p>
            <div className="actions">
              <button type="button" className="btn" data-variant="primary" onClick={() => void copyKey()}>{copied ? c.copied : c.copy}</button>
              <button type="button" className="btn" data-variant="quiet" onClick={() => { setCreating(false); setFresh(null); }}>{c.done}</button>
            </div>
          </div>
        ) : (
          <form onSubmit={create}>
            <label className="field">
              <span>{c.name}</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder={c.namePlaceholder} maxLength={120} required />
            </label>
            <div className="actions">
              <button type="submit" className="btn" data-variant="primary" disabled={busy || !name.trim()}>
                {busy ? c.creating : c.create}
              </button>
            </div>
          </form>
        )}
      </Sheet>
      <ConfirmDialog request={confirming} onClose={closeConfirm} />
    </SalesShell>
  );
}
