"use client";

import Script from "next/script";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { NotificationBell } from "@/lib/NotificationBell";

type Membership = { license_id: string; license_code: string; company_name: string };
type Role = {
  role_name: string;
  is_owner: boolean;
  permission_keys: string[];
};

type CatalogEntry = {
  key: string;
  group?: string | null;
  label?: { th?: string; en?: string } | null;
};
type LiffApi = {
  init(config: { liffId: string; withLoginOnExternalBrowser: boolean }): Promise<void>;
  isLoggedIn(): boolean;
  login(): void;
  getIDToken(): string | null;
};

function getLiff(): LiffApi | undefined {
  return (window as Window & { liff?: LiffApi }).liff;
}

export default function RoleManagement({ liffId }: { liffId: string }) {
  const [token, setToken] = useState("");
  const { t, locale } = useLanguage();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [roles, setRoles] = useState<Role[]>([]);
  const [roleName, setRoleName] = useState("");
  // A set, not a comma-separated string. The old textarea required a shop
  // owner to know that "customer.read" exists and to spell it exactly; a
  // typo granted nothing and said nothing.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [editingRoleName, setEditingRoleName] = useState("");
  const [settingKey, setSettingKey] = useState("");
  const [settingValue, setSettingValue] = useState("");
  const [status, setStatus] = useState(t.liff.starting);
  const [busy, setBusy] = useState(false);

  // A permission key is for the API; a person reads the catalogue label.
  const permissionLabel = (key: string) => {
    const entry = catalog.find((row) => row.key === key);
    return entry?.label?.[locale] ?? entry?.label?.th ?? key;
  };

  const headers = useCallback(
    () => ({
      "Content-Type": "application/json",
      "X-Liff-ID-Token": token,
      "X-Liff-Audience": "sales",
      "X-License-Id": licenseId,
    }),
    [token, licenseId],
  );

  const loadRoles = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/roles`, {
      headers: headers(),
    });
    if (!response.ok) {
      throw new Error(t.role.loadFailed.replace("{status}", String(response.status)));
    }
    setRoles((await response.json()) as Role[]);

    // Loaded alongside the roles: the catalogue is platform-wide and does
    // not change while someone is editing, so once is enough.
    try {
      const catalogResponse = await fetch("/api/phase2/permissions/catalog", {
        headers: headers(),
      });
      if (catalogResponse.ok) {
        setCatalog((await catalogResponse.json()) as CatalogEntry[]);
      }
    } catch {
      // A missing catalogue leaves the form with nothing to pick, which is
      // visible. A hardcoded fallback list would be worse: it would drift
      // from what the server actually enforces.
    }
  }, [headers, licenseId, t, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void loadRoles().catch((error: unknown) =>
      setStatus(error instanceof Error ? error.message : t.dashboard.loadFailed),
    );
  }, [licenseId, loadRoles, t, token]);

  const initialize = useCallback(async () => {
    const liff = getLiff();
    if (!liffId || !liff) {
      setStatus(t.liff.notConfigured);
      return;
    }
    try {
      await liff.init({ liffId, withLoginOnExternalBrowser: true });
      if (!liff.isLoggedIn()) {
        liff.login();
        return;
      }
      const idToken = liff.getIDToken();
      if (!idToken) throw new Error(t.liff.initFailed);
      const response = await fetch("/api/liff/sales/me", {
        headers: { "X-Liff-ID-Token": idToken },
      });
      if (!response.ok) throw new Error(`${t.liff.initFailed} (${response.status})`);
      const profile = (await response.json()) as { memberships: Membership[] };
      setToken(idToken);
      setMemberships(profile.memberships);
      setLicenseId(profile.memberships[0]?.license_id ?? "");
      setStatus(profile.memberships.length ? t.role.ready : t.liff.noCompany);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : t.liff.initFailed);
    }
  }, [liffId, t]);

  async function saveRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const permissionKeys = Array.from(selected);
    const target = editingRoleName
      ? `/api/phase2/licenses/${licenseId}/roles/${encodeURIComponent(editingRoleName)}`
      : `/api/phase2/licenses/${licenseId}/roles`;
    setBusy(true);
    try {
      const response = await fetch(target, {
        method: editingRoleName ? "PATCH" : "POST",
        headers: headers(),
        body: JSON.stringify({ role_name: roleName, permission_keys: permissionKeys }),
      });
      if (!response.ok) {
        setStatus(t.role.saveFailed.replace("{status}", String(response.status)));
        return;
      }
      setRoleName("");
      setSelected(new Set());
      setEditingRoleName("");
      setStatus(t.role.saved);
      await loadRoles();
    } finally {
      setBusy(false);
    }
  }

  function editRole(role: Role) {
    setEditingRoleName(role.role_name);
    setRoleName(role.role_name);
    setSelected(new Set(role.permission_keys));
    setStatus(t.role.editing.replace("{name}", role.role_name));
  }

  function cancelEdit() {
    setEditingRoleName("");
    setRoleName("");
    setSelected(new Set());
    setStatus(t.role.editCancelled);
  }

  async function deleteRole(role: Role) {
    // Destructive and one tap away: ask, and lock the buttons while it runs.
    if (!window.confirm(t.role.confirmDelete.replace("{name}", role.role_name))) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/roles/${encodeURIComponent(role.role_name)}`,
        { method: "DELETE", headers: headers() },
      );
      setStatus(
        response.ok
          ? t.role.deleted
          : t.role.deleteFailed.replace("{status}", String(response.status)),
      );
      if (response.ok) await loadRoles();
    } finally {
      setBusy(false);
    }
  }

  async function saveSetting(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    let parsed: unknown = settingValue;
    try {
      parsed = JSON.parse(settingValue);
    } catch {
      // Plain text is a valid JSONB value and stays a string.
    }
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/settings/${encodeURIComponent(settingKey)}`,
        {
          method: "PUT",
          headers: headers(),
          body: JSON.stringify({ setting_value: parsed }),
        },
      );
      setStatus(
        response.ok
          ? t.licenseSetting.saved
          : t.licenseSetting.saveFailed.replace("{status}", String(response.status)),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <Script
        src="https://static.line-scdn.net/liff/edge/versions/2.29.2/sdk.js"
        strategy="afterInteractive"
        onReady={() => void initialize()}
        onError={() => setStatus(t.liff.sdkLoadFailed)}
      />
      <h1>{t.role.title}</h1>
      <LanguageSwitcher />
      {token && licenseId && (
        <NotificationBell idToken={token} licenseId={licenseId} />
      )}
      <p aria-live="polite">{status}</p>

      {memberships.length > 1 && (
        <label className="field">
          <span>{t.dashboard.company}</span>
          <select value={licenseId} onChange={(event) => setLicenseId(event.target.value)}>
            {memberships.map((membership) => (
              <option key={membership.license_id} value={membership.license_id}>
                {membership.company_name} ({membership.license_code})
              </option>
            ))}
          </select>
        </label>
      )}

      <section>
        <h2>{t.role.permissionMatrix}</h2>
        {roles.map((role) => (
          <article key={role.role_name} className="card" style={{ margin: "8px 0" }}>
            <div className="card-title">
              {role.role_name}
              {role.is_owner && (
                <span className="badge" data-stage="won">
                  {t.role.protectedOwner}
                </span>
              )}
            </div>
            <p className="card-meta">
              {role.permission_keys.map(permissionLabel).join(", ") || t.role.noPermissions}
            </p>
            {!role.is_owner && (
              <div className="card-actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => editRole(role)}
                  disabled={busy}
                >
                  {t.role.editRole}
                </button>
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => void deleteRole(role)}
                  disabled={busy}
                >
                  {t.role.deleteRole}
                </button>
              </div>
            )}
          </article>
        ))}
      </section>

      <form onSubmit={saveRole} style={{ display: "grid", gap: 8, marginTop: 24 }}>
        <h2>
          {editingRoleName
            ? t.role.editingTitle.replace("{name}", editingRoleName)
            : t.role.createCustomRole}
        </h2>
        <label className="field">
          <span>{t.role.roleName}</span>
          <input value={roleName} onChange={(event) => setRoleName(event.target.value)} required />
        </label>
        <fieldset style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12 }}>
          <legend style={{ fontSize: 13.5, color: "var(--ink-soft)" }}>
            {t.role.permissionKeys}
          </legend>
          {catalog.length === 0 ? (
            <p className="card-meta">{t.role.catalogUnavailable}</p>
          ) : (
            Object.entries(
              catalog.reduce<Record<string, CatalogEntry[]>>((groups, entry) => {
                // Platform-admin keys are never a tenant's to grant.
                if (entry.key.startsWith("platform.admin.")) return groups;
                const group = entry.group ?? "general";
                groups[group] = [...(groups[group] ?? []), entry];
                return groups;
              }, {}),
            ).map(([group, entries]) => (
              <div key={group} style={{ marginBottom: 10 }}>
                <p
                  style={{
                    margin: "6px 0 4px",
                    fontSize: 12.5,
                    color: "var(--ink-faint)",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                  }}
                >
                  {group}
                </p>
                {entries.map((entry) => (
                  <label
                    key={entry.key}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      // 44px so it is a real tap target on a phone, not a
                      // 13px checkbox someone has to aim at.
                      minHeight: 44,
                      fontSize: 15,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(entry.key)}
                      style={{ width: 20, height: 20, flex: "none" }}
                      onChange={(event) => {
                        const next = new Set(selected);
                        if (event.target.checked) next.add(entry.key);
                        else next.delete(entry.key);
                        setSelected(next);
                      }}
                    />
                    <span>
                      {entry.label?.[locale] ?? entry.label?.th ?? entry.key}
                      {/* The key itself, quietly. Someone reading the API
                          docs or a support thread needs to connect the two. */}
                      <span
                        className="code"
                        style={{ marginLeft: 6, fontSize: 11.5, opacity: 0.6 }}
                      >
                        {entry.key}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            ))
          )}
          <p className="card-meta" style={{ marginTop: 8 }}>
            {t.role.selectedCount.replace("{count}", String(selected.size))}
          </p>
        </fieldset>
        <div className="actions">
          <button type="submit" className="btn" data-variant="primary" disabled={!licenseId || busy}>
            {busy
              ? t.dashboard.saving
              : editingRoleName
                ? t.role.saveEdit
                : t.role.createButton}
          </button>
          {editingRoleName && (
            <button type="button" className="btn" data-variant="quiet" onClick={cancelEdit} disabled={busy}>
              {t.common.cancel}
            </button>
          )}
        </div>
      </form>

      <form onSubmit={saveSetting} style={{ display: "grid", gap: 8, marginTop: 32 }}>
        <h2>{t.licenseSetting.title}</h2>
        <label className="field">
          <span>{t.licenseSetting.settingKey}</span>
          <input value={settingKey} onChange={(event) => setSettingKey(event.target.value)} required />
        </label>
        <label className="field">
          <span>{t.licenseSetting.settingValue}</span>
          <textarea value={settingValue} onChange={(event) => setSettingValue(event.target.value)} required />
        </label>
        <div className="actions">
          <button type="submit" className="btn" data-variant="primary" disabled={!licenseId || busy}>
            {busy ? t.dashboard.saving : t.licenseSetting.saveButton}
          </button>
        </div>
      </form>
    </main>
  );
}
