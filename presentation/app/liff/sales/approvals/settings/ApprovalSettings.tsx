"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../../_components";
import { FieldRow } from "../../../_field-row";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../../../_shared";

type Workflow = {
  rules_json?: { steps?: { order: number; approver_type: string; approver_ref: string }[] } | null;
  summary?: string;
  updated_at?: string | null;
};

/**
 * The approval flow, in words — Phase 14-C, parity with chat's
 * "ตั้งการอนุมัติ …" (owner decision 3). The policy text goes through the
 * same model call the chat command uses (the route does it), and the
 * flow comes back described from its structure, so what is shown is
 * what will run.
 */
export default function ApprovalSettings({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [licenseId, setLicenseId] = useState("");
  const [token, setToken] = useState("");
  const [policy, setPolicy] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      const response = await fetch(
        `/api/phase2/licenses/${license}/approval-workflows/service_report`,
        { headers: proxyHeaders(currentToken, license, "sales") },
      );
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      setWorkflow((await response.json()) as Workflow);
      say("");
    },
    [licenseId, say, t, token],
  );

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, "sales");
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      setToken(session.token);
      setLicenseId(license);
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setPermissions(await fetchPermissions(session.token, license, "sales"));
      await load(session.token, license);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  async function save() {
    if (!policy.trim()) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/approval-workflows/service_report`,
        {
          method: "PUT",
          headers: {
            ...proxyHeaders(token, licenseId, "sales"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ policy: policy.trim() }),
        },
      );
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as
          | { detail?: { problems?: string[] } | string }
          | null;
        const problems =
          body && typeof body.detail === "object" && body.detail?.problems?.length
            ? body.detail.problems.join(" · ")
            : "";
        say(
          problems
            ? t.dashboard.approvals.notUnderstood.replace("{problems}", problems)
            : response.status === 403
              ? t.dashboard.noPermission
              : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      setWorkflow((await response.json()) as Workflow);
      setPolicy("");
      say(t.dashboard.approvals.saved, "ok");
    } catch {
      say(t.dashboard.related.actionFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  const canManage = permissions.has("approval.manage");

  return (
    <AppShell
      title={t.dashboard.approvals.settingsTitle}
      back="/liff/sales/approvals"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="page-intro">{t.dashboard.approvals.settingsIntro}</p>

      <section className="section">
        <div className="section-head">
          <h2>{t.dashboard.approvals.currentFlow}</h2>
        </div>
        {workflow && (
          <pre className="flow-summary">{workflow.summary ?? ""}</pre>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h2>{t.dashboard.approvals.policyLabel}</h2>
        </div>
        {canManage ? (
          <dl className="fields">
            <FieldRow label={t.dashboard.approvals.policyLabel}>
              {(id) => (
                <>
                  <textarea
                    id={id}
                    rows={3}
                    value={policy}
                    placeholder={t.dashboard.approvals.policyHint}
                    onChange={(event) => setPolicy(event.target.value)}
                    aria-describedby={`${id}-hint`}
                  />
                  <span id={`${id}-hint`} className="hint">
                    {t.dashboard.approvals.policyHint}
                  </span>
                </>
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={busy || !policy.trim()}
                onClick={() => void save()}
              >
                {busy ? t.dashboard.saving : t.dashboard.approvals.savePolicy}
              </button>
            </div>
          </dl>
        ) : (
          <p className="card-meta">{t.dashboard.approvals.readOnly}</p>
        )}
      </section>
    </AppShell>
  );
}
