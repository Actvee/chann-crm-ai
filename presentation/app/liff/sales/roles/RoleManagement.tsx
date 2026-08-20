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
  const [permissionText, setPermissionText] = useState("");
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
    const permissionKeys = permissionText
      .split(",")
      .map((key) => key.trim())
      .filter(Boolean);
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
    setPermissionText("");
    setEditingRoleName("");
    setStatus(`${editingRoleName ? "แก้" : "สร้าง"} role สำเร็จ`);
    await loadRoles();
  }

  function editRole(role: Role) {
    setEditingRoleName(role.role_name);
    setRoleName(role.role_name);
    setPermissionText(role.permission_keys.join(", "));
    setStatus(`กำลังแก้ role ${role.role_name}`);
  }

  function cancelEdit() {
    setEditingRoleName("");
    setRoleName("");
    setPermissionText("");
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
        <label>
          {t.role.permissionKeys}
          <textarea
            value={permissionText}
            onChange={(event) => setPermissionText(event.target.value)}
            placeholder="customer.read, deal.create"
            required
          />
        </label>
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
