"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";
import { ConfirmDialog, useConfirm } from "../../_confirm";
import { Sheet } from "../../_sheet";

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

type Group = { key: string; entries: CatalogEntry[] };

/** How many group names a role row shows before "+n กลุ่ม". */
const ROW_GROUPS = 3;

/**
 * Roles: who may do what.
 *
 * Owner, 22 ก.ย. 2569: "หน้าจัดการสิทธิ์ให้ออกแบบใหม่ … เน้นซ้อนสิทธิ์ต่างๆไว้ก่อน
 * และแสดงเฉพาะตอนดูรายละเอียด สร้างหรือแก้ไข". The page used to print every
 * permission of every role as one comma-joined paragraph and then the
 * whole 50-box pick-list under it, always — a wall the eye could not
 * scan (ui-ux-pro-max: progressive disclosure — reveal complex options
 * progressively, never all upfront).
 *
 * Now: a compact row per role (name, how many permissions, the first
 * few groups, "+n"), and the permissions themselves only in the detail
 * sheet, or in the editor sheet where every group is a collapsed
 * section that opens on its own (native <details>, so the keyboard and
 * a screen reader get it for free). The "+n" is itself the way into the
 * detail — a chip that hides values must be operable, not decorative.
 */
export default function RoleManagement({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const s = useSalesText();
  const failureText = useFailureText();
  const [roles, setRoles] = useState<Role[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [busy, setBusy] = useState(false);

  // The one sheet that is open: a role's detail, or the editor (a new
  // role when `editing` is null).
  const [viewing, setViewing] = useState<Role | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [roleName, setRoleName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [settingKey, setSettingKey] = useState("");
  const [settingValue, setSettingValue] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;
  const headers = useCallback(() => proxyHeaders(token, licenseId), [token, licenseId]);

  const groupLabel = (key: string) =>
    (t.role.groups as Record<string, string>)[key] ?? key;
  const permissionLabel = (entry: CatalogEntry) =>
    entry.label?.[locale] ?? entry.label?.th ?? entry.key;

  // The catalogue, grouped the way the server groups it (the key's
  // prefix). Platform-admin keys are never a tenant's to grant.
  const groups: Group[] = useMemo(() => {
    const byGroup = new Map<string, CatalogEntry[]>();
    for (const entry of catalog) {
      if (entry.key.startsWith("platform.admin.")) continue;
      const key = entry.group ?? "general";
      byGroup.set(key, [...(byGroup.get(key) ?? []), entry]);
    }
    return Array.from(byGroup, ([key, entries]) => ({ key, entries }));
  }, [catalog]);

  /** The groups a role touches, in catalogue order, with the entries it holds. */
  const groupsOf = (role: Role): Group[] => {
    const held = new Set(role.permission_keys);
    return groups
      .map((g) => ({ key: g.key, entries: g.entries.filter((e) => held.has(e.key)) }))
      .filter((g) => g.entries.length > 0);
  };

  const loadRoles = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/roles`, { headers: headers() });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : t.role.loadFailed.replace("{status}", String(response.status)),
      );
    }
    setRoles((await response.json()) as Role[]);
    // The catalogue is platform-wide and does not change while someone
    // is editing, so once is enough.
    try {
      const catalogResponse = await fetch("/api/phase2/permissions/catalog", { headers: headers() });
      if (catalogResponse.ok) setCatalog((await catalogResponse.json()) as CatalogEntry[]);
    } catch {
      // A missing catalogue leaves the editor with nothing to pick, which
      // is visible. A hardcoded list would drift from what is enforced.
    }
    say(t.role.ready, "ok");
  }, [headers, licenseId, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    void loadRoles().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [session.ready, loadRoles, say, t]);

  function openCreate() {
    setEditing(null);
    setRoleName("");
    setSelected(new Set());
    setViewing(null);
    setEditorOpen(true);
  }

  function openEdit(role: Role) {
    setEditing(role);
    setRoleName(role.role_name);
    setSelected(new Set(role.permission_keys));
    setViewing(null);
    setEditorOpen(true);
  }

  function closeEditor() {
    setEditorOpen(false);
    setEditing(null);
  }

  async function saveRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = editing
      ? `/api/phase2/licenses/${licenseId}/roles/${encodeURIComponent(editing.role_name)}`
      : `/api/phase2/licenses/${licenseId}/roles`;
    setBusy(true);
    try {
      const response = await fetch(target, {
        method: editing ? "PATCH" : "POST",
        headers: headers(),
        body: JSON.stringify({ role_name: roleName, permission_keys: Array.from(selected) }),
      });
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      closeEditor();
      say(t.role.saved, "ok");
      await loadRoles();
    } finally {
      setBusy(false);
    }
  }

  async function deleteRole(role: Role) {
    // Destructive and one tap away: ask, and lock the buttons while it runs.
    const ok = await ask({
      action: t.common.delete,
      target: role.role_name,
      affects: [t.role.deleteAffects],
      permanent: true,
      confirmLabel: t.role.confirmDeleteButton,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/roles/${encodeURIComponent(role.role_name)}`,
        { method: "DELETE", headers: headers() },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      setViewing(null);
      say(t.role.deleted, "ok");
      await loadRoles();
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
        { method: "PUT", headers: headers(), body: JSON.stringify({ setting_value: parsed }) },
      );
      say(response.ok ? t.licenseSetting.saved : await failureText(response), response.ok ? "ok" : "error");
    } finally {
      setBusy(false);
    }
  }

  function setGroup(group: Group, on: boolean) {
    const next = new Set(selected);
    for (const entry of group.entries) {
      if (on) next.add(entry.key);
      else next.delete(entry.key);
    }
    setSelected(next);
  }

  // The keys the routes check (review C7): roles are role.manage,
  // settings are setting.manage. A suspended shop edits neither.
  const canManageRoles = !session.suspended && permissions.has("role.manage");
  const canManageSettings = !session.suspended && permissions.has("setting.manage");

  const rowSummary = (role: Role) => {
    if (role.is_owner) return null;
    const held = groupsOf(role);
    const shown = held.slice(0, ROW_GROUPS);
    const more = held.length - shown.length;
    return (
      <span className="role-row-groups">
        {shown.map((g) => (
          <span key={g.key} className="chip" data-tone="muted">{groupLabel(g.key)}</span>
        ))}
        {more > 0 && (
          <span className="chip" data-tone="muted">{t.role.moreGroups.replace("{count}", String(more))}</span>
        )}
      </span>
    );
  };

  return (
    <SalesShell
      session={session}
      title={t.role.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {/* Roles say what a person may do; the members page says who holds
          one. Linked both ways so neither is a page you have to know about. */}
      <p className="card-meta" style={{ margin: "0 0 12px" }}>
        <Link href="/liff/sales/members">{t.dashboard.members.membersLink} →</Link>
      </p>

      {session.ready && !canManageRoles && !session.suspended && (
        <p className="card-meta" style={{ marginBottom: 12 }}>{s.roles.readOnly}</p>
      )}

      <div className="list-head">
        <span className="count">{t.role.rolesCount.replace("{count}", String(roles.length))}</span>
        {canManageRoles && (
          <button type="button" className="btn" data-variant="primary" onClick={openCreate} disabled={busy}>
            {t.role.createCustomRole}
          </button>
        )}
      </div>

      <ul className="role-list">
        {roles.map((role) => (
          <li key={role.role_name} className="role-row">
            {/* The whole name column opens the detail; the actions are
                their own buttons so a thumb never opens the sheet by
                accident while aiming at "แก้ไข". */}
            <button type="button" className="role-row-main" onClick={() => setViewing(role)}>
              <span className="role-row-name">
                {role.role_name}
                {role.is_owner && <span className="badge" data-stage="won">{t.role.protectedOwner}</span>}
              </span>
              <span className="card-meta role-row-meta">
                {role.is_owner
                  ? t.role.allPermissions
                  : t.role.permissionCount.replace("{count}", String(role.permission_keys.length))}
                {rowSummary(role)}
              </span>
            </button>
            <div className="role-row-actions">
              <button type="button" className="btn" data-variant="quiet" onClick={() => setViewing(role)}>
                {t.role.detail}
              </button>
              {!role.is_owner && canManageRoles && (
                <button type="button" className="btn" data-variant="quiet" onClick={() => openEdit(role)} disabled={busy}>
                  {t.role.editRole}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>

      {/* Detail: the permissions, grouped, read-only — the only place
          they are spelled out besides the editor. */}
      <Sheet open={viewing !== null} title={viewing?.role_name ?? ""} onClose={() => setViewing(null)}>
        {viewing && (
          <>
            {viewing.is_owner ? (
              <p className="card-meta">{t.role.allPermissions}</p>
            ) : groupsOf(viewing).length === 0 ? (
              <p className="card-meta">{t.role.noPermissions}</p>
            ) : (
              <>
                <p className="card-meta" style={{ margin: "0 0 10px" }}>
                  {t.role.permissionCount.replace("{count}", String(viewing.permission_keys.length))}
                </p>
                {groupsOf(viewing).map((g) => (
                  <div key={g.key} className="perm-view-group">
                    <h3>{groupLabel(g.key)}</h3>
                    <ul>
                      {g.entries.map((entry) => (
                        <li key={entry.key}>{permissionLabel(entry)}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </>
            )}
            {!viewing.is_owner && canManageRoles && (
              <div className="actions" style={{ marginTop: 16 }}>
                <button type="button" className="btn" data-variant="primary" onClick={() => openEdit(viewing)} disabled={busy}>
                  {t.role.editRole}
                </button>
                <button type="button" className="btn" data-variant="danger" onClick={() => void deleteRole(viewing)} disabled={busy}>
                  {t.role.deleteRole}
                </button>
              </div>
            )}
          </>
        )}
      </Sheet>

      {/* Editor: name, then one collapsed section per group. A group's
          summary says how many of its permissions are on, so the whole
          role can be read without opening anything; open a group to
          change it, or take the whole group in one tap. */}
      <Sheet
        open={editorOpen}
        title={editing ? t.role.editingTitle.replace("{name}", editing.role_name) : t.role.createCustomRole}
        onClose={closeEditor}
      >
        <form onSubmit={saveRole} className="perm-editor">
          <label className="field">
            <span>{t.role.roleName}</span>
            <input value={roleName} onChange={(event) => setRoleName(event.target.value)} required />
          </label>
          <p className="card-meta" style={{ margin: "4px 0 0" }}>{t.role.editorHint}</p>
          {catalog.length === 0 ? (
            <p className="card-meta">{t.role.catalogUnavailable}</p>
          ) : (
            groups.map((g) => {
              const on = g.entries.filter((e) => selected.has(e.key)).length;
              return (
                <details key={g.key} className="perm-group">
                  <summary>
                    <span className="perm-group-name">{groupLabel(g.key)}</span>
                    <span className="chip" data-tone={on > 0 ? "live" : "muted"}>
                      {t.role.groupProgress
                        .replace("{selected}", String(on))
                        .replace("{total}", String(g.entries.length))}
                    </span>
                  </summary>
                  <div className="perm-group-tools">
                    <button type="button" className="btn" data-variant="quiet" onClick={() => setGroup(g, true)}>
                      {t.role.selectGroup}
                    </button>
                    <button type="button" className="btn" data-variant="quiet" onClick={() => setGroup(g, false)} disabled={on === 0}>
                      {t.role.clearGroup}
                    </button>
                  </div>
                  {g.entries.map((entry) => (
                    <label key={entry.key} className="perm-option">
                      <input
                        type="checkbox"
                        checked={selected.has(entry.key)}
                        onChange={(event) => {
                          const next = new Set(selected);
                          if (event.target.checked) next.add(entry.key);
                          else next.delete(entry.key);
                          setSelected(next);
                        }}
                      />
                      <span>
                        {permissionLabel(entry)}
                        {/* The key itself, quietly, for anyone reading a
                            support thread or the API docs. */}
                        <span className="code perm-option-key">{entry.key}</span>
                      </span>
                    </label>
                  ))}
                </details>
              );
            })
          )}
          <div className="perm-foot">
            <span className="card-meta" aria-live="polite">
              {t.role.selectedCount.replace("{count}", String(selected.size))}
            </span>
            <div className="actions">
              <button type="button" className="btn" data-variant="quiet" onClick={closeEditor} disabled={busy}>
                {t.common.cancel}
              </button>
              <button type="submit" className="btn" data-variant="primary" disabled={!licenseId || busy}>
                {busy ? t.dashboard.saving : editing ? t.role.saveEdit : t.role.createButton}
              </button>
            </div>
          </div>
        </form>
      </Sheet>

      {/* A raw key/value form is a developer's tool, not the page's job:
          folded away under its own heading so it never competes with the
          roles for attention. */}
      {canManageSettings && (
        <details className="perm-group" style={{ marginTop: 24 }}>
          <summary>
            <span className="perm-group-name">{t.role.advancedSettings}</span>
          </summary>
          <form onSubmit={saveSetting} style={{ display: "grid", gap: 8, padding: "4px 0 8px" }}>
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
        </details>
      )}
      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={Boolean(busy)} />
    </SalesShell>
  );
}
