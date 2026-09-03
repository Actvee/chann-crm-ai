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
  const { t } = useLanguage();
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
  const [status, setStatus] = useState("กำลังเริ่ม LIFF…");

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
    if (!response.ok) throw new Error(`โหลด Permission Matrix ไม่สำเร็จ (${response.status})`);
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
  }, [headers, licenseId, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void loadRoles().catch((error: unknown) =>
      setStatus(error instanceof Error ? error.message : "โหลด role ไม่สำเร็จ"),
    );
  }, [licenseId, loadRoles, token]);

  const initialize = useCallback(async () => {
    const liff = getLiff();
    if (!liffId || !liff) {
      setStatus("NEXT_PUBLIC_LIFF_SALES_ID is REQUIRED_NOT_CONFIGURED");
      return;
    }
    try {
      await liff.init({ liffId, withLoginOnExternalBrowser: true });
      if (!liff.isLoggedIn()) {
        liff.login();
        return;
      }
      const idToken = liff.getIDToken();
      if (!idToken) throw new Error("LIFF did not return an ID token");
      const response = await fetch("/api/liff/sales/me", {
        headers: { "X-Liff-ID-Token": idToken },
      });
      if (!response.ok) throw new Error(`authentication failed (${response.status})`);
      const profile = (await response.json()) as { memberships: Membership[] };
      setToken(idToken);
      setMemberships(profile.memberships);
      setLicenseId(profile.memberships[0]?.license_id ?? "");
      setStatus(profile.memberships.length ? "พร้อมจัดการสิทธิ์" : "ยังไม่พบบริษัทที่ผูกไว้");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "LIFF initialization failed");
    }
  }, [liffId]);

  async function saveRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const permissionKeys = Array.from(selected);
    const target = editingRoleName
      ? `/api/phase2/licenses/${licenseId}/roles/${encodeURIComponent(editingRoleName)}`
      : `/api/phase2/licenses/${licenseId}/roles`;
    const response = await fetch(target, {
      method: editingRoleName ? "PATCH" : "POST",
      headers: headers(),
      body: JSON.stringify({ role_name: roleName, permission_keys: permissionKeys }),
    });
    if (!response.ok) {
      setStatus(`${editingRoleName ? "แก้" : "สร้าง"} role ไม่สำเร็จ (${response.status})`);
      return;
    }
    setRoleName("");
    setSelected(new Set());
    setEditingRoleName("");
    setStatus(`${editingRoleName ? "แก้" : "สร้าง"} role สำเร็จ`);
    await loadRoles();
  }

  function editRole(role: Role) {
    setEditingRoleName(role.role_name);
    setRoleName(role.role_name);
    setSelected(new Set(role.permission_keys));
    setStatus(`กำลังแก้ role ${role.role_name}`);
  }

  function cancelEdit() {
    setEditingRoleName("");
    setRoleName("");
    setSelected(new Set());
    setStatus("ยกเลิกการแก้ role แล้ว");
  }

  async function deleteRole(role: Role) {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/roles/${encodeURIComponent(role.role_name)}`,
      { method: "DELETE", headers: headers() },
    );
    setStatus(response.ok ? "ลบ role สำเร็จ" : `ลบ role ไม่สำเร็จ (${response.status})`);
    if (response.ok) await loadRoles();
  }

  async function saveSetting(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    let parsed: unknown = settingValue;
    try {
      parsed = JSON.parse(settingValue);
    } catch {
      // Plain text is a valid JSONB value and stays a string.
    }
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/settings/${encodeURIComponent(settingKey)}`,
      {
        method: "PUT",
        headers: headers(),
        body: JSON.stringify({ setting_value: parsed }),
      },
    );
    setStatus(response.ok ? "บันทึก setting สำเร็จ" : `บันทึกไม่สำเร็จ (${response.status})`);
  }

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <Script
        src="https://static.line-scdn.net/liff/edge/versions/2.29.2/sdk.js"
        strategy="afterInteractive"
        onReady={() => void initialize()}
        onError={() => setStatus("LIFF SDK load failed")}
      />
      <h1>{t.role.title}</h1>
      <LanguageSwitcher />
      {token && licenseId && (
        <NotificationBell idToken={token} licenseId={licenseId} />
      )}
      <p aria-live="polite">{status}</p>

      {memberships.length > 1 && (
        <label>
          บริษัท
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
          <article key={role.role_name} style={{ border: "1px solid #ddd", padding: 12, margin: "8px 0" }}>
            <strong>{role.role_name}</strong>{role.is_owner ? " — protected owner" : ""}
            <p>{role.permission_keys.join(", ") || "ไม่มี permission"}</p>
            {!role.is_owner && (
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" onClick={() => editRole(role)}>แก้ role</button>
                <button type="button" onClick={() => void deleteRole(role)}>ลบ role</button>
              </div>
            )}
          </article>
        ))}
      </section>

      <form onSubmit={saveRole} style={{ display: "grid", gap: 8, marginTop: 24 }}>
        <h2>{editingRoleName ? `แก้ Custom Role: ${editingRoleName}` : t.role.createCustomRole}</h2>
        <label>
          {t.role.roleName}
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
                      {entry.label?.th ?? entry.key}
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
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" disabled={!licenseId}>
            {editingRoleName ? "บันทึกการแก้ไข" : "สร้าง role"}
          </button>
          {editingRoleName && <button type="button" onClick={cancelEdit}>ยกเลิก</button>}
        </div>
      </form>

      <form onSubmit={saveSetting} style={{ display: "grid", gap: 8, marginTop: 32 }}>
        <h2>{t.licenseSetting.title}</h2>
        <label>
          {t.licenseSetting.settingKey}
          <input value={settingKey} onChange={(event) => setSettingKey(event.target.value)} required />
        </label>
        <label>
          {t.licenseSetting.settingValue}
          <textarea value={settingValue} onChange={(event) => setSettingValue(event.target.value)} required />
        </label>
        <button type="submit" disabled={!licenseId}>{t.licenseSetting.saveButton}</button>
      </form>
    </main>
  );
}
