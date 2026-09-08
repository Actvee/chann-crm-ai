"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import { NotificationBell } from "@/lib/NotificationBell";

import { Badge, Empty } from "../_components";
import { describeFailure, readFailure, useFormatters } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";

type Channel = "sales" | "technician";
type MemberStatus = "active" | "removed";

/** One (person, OA) membership as GET licenses/{id}/members?include_removed=1 sends it. */
type MemberRow = {
  chann_uid: string;
  display_name: string;
  role: string;
  channel: Channel;
  status: MemberStatus;
  joined_at?: string | null;
  is_owner?: boolean;
  phone?: string | null;
};

type Role = {
  role_name: string;
  is_owner: boolean;
};

/** A person with every OA membership they hold — the same chann_uid may
 *  be a salesperson on one OA and a technician on the other. */
type Person = {
  chann_uid: string;
  display_name: string;
  phone: string;
  isOwner: boolean;
  rows: MemberRow[];
};

const CHANNELS: Channel[] = ["sales", "technician"];

/**
 * Shop members (owner, 8 Sep 2026: "the dashboard cannot edit or remove a
 * user at all").
 *
 * The roles page defines what a role may do; this page is who holds one.
 * Every row is one membership on one OA, so removing a technician from
 * the technician LINE leaves their sales membership (if any) untouched.
 * Reading the list needs one of the people-managing keys server-side; the
 * writes are all member.manage, so the page gates on that one and shows
 * everyone else a plain "no permission" rather than a list of buttons
 * that would 403.
 */
export default function MemberManagement({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const s = useSalesText();
  const m = t.dashboard.members;
  const { shortDate } = useFormatters();

  const [members, setMembers] = useState<MemberRow[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyKey, setBusyKey] = useState("");
  const [channel, setChannel] = useState<"all" | Channel>("all");
  const [showRemoved, setShowRemoved] = useState(false);
  const [query, setQuery] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;
  const hasKey = permissions.has("member.manage");
  const canManage = !session.suspended && hasKey;

  const headers = useCallback(() => proxyHeaders(token, licenseId), [token, licenseId]);

  /** The API's own reason, in the reader's words, with the server's
   *  message kept when the generic sentence would hide it (the contract
   *  sends {detail: {code, message}} for every refusal). */
  const failureText = useCallback(
    async (response: Response): Promise<string> => {
      const failure = await readFailure(response);
      const text = describeFailure(failure, t, s);
      return failure.message && !text.includes(failure.message)
        ? `${text} — ${failure.message}`
        : text;
    },
    [s, t],
  );

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/members?include_removed=1`,
      { headers: headers() },
    );
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    setMembers(normalise(await response.json()));

    // The role names for the dropdown. A missing list leaves the select
    // with only the roles already in use, which is visible, not wrong.
    try {
      const rolesResponse = await fetch(`/api/phase2/licenses/${licenseId}/roles`, {
        headers: headers(),
      });
      if (rolesResponse.ok) {
        const rows = (await rolesResponse.json()) as unknown;
        setRoles(Array.isArray(rows) ? (rows as Role[]) : []);
      }
    } catch {
      // Handled by the fallback above.
    }
    say(m.ready, "ok");
  }, [headers, licenseId, m.ready, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    if (!hasKey) {
      say("");
      return;
    }
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [session.ready, hasKey, load, say, t]);

  const channelLabel = useCallback(
    (value: Channel) => (value === "technician" ? m.technician : m.sales),
    [m.sales, m.technician],
  );

  // Grouped by person, owner first, then by name — the list is per shop
  // and small, so grouping in the browser costs nothing.
  const people = useMemo<Person[]>(() => {
    const byUid = new Map<string, Person>();
    for (const row of members) {
      const person = byUid.get(row.chann_uid) ?? {
        chann_uid: row.chann_uid,
        display_name: row.display_name || row.chann_uid,
        phone: "",
        isOwner: false,
        rows: [],
      };
      if (!person.phone && row.phone) person.phone = row.phone;
      if (row.display_name && person.display_name === row.chann_uid) {
        person.display_name = row.display_name;
      }
      if (row.is_owner) person.isOwner = true;
      person.rows.push(row);
      byUid.set(row.chann_uid, person);
    }
    return Array.from(byUid.values())
      .map((person) => ({
        ...person,
        rows: [...person.rows].sort(
          (a, b) => CHANNELS.indexOf(a.channel) - CHANNELS.indexOf(b.channel),
        ),
      }))
      .sort((a, b) =>
        a.isOwner !== b.isOwner
          ? a.isOwner ? -1 : 1
          : a.display_name.localeCompare(b.display_name),
      );
  }, [members]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return people
      .map((person) => ({
        ...person,
        rows: person.rows.filter(
          (row) =>
            (channel === "all" || row.channel === channel) &&
            (showRemoved || row.status !== "removed"),
        ),
      }))
      .filter(
        (person) =>
          person.rows.length > 0 &&
          (!needle ||
            person.display_name.toLowerCase().includes(needle) ||
            person.phone.toLowerCase().includes(needle) ||
            person.chann_uid.toLowerCase().includes(needle)),
      );
  }, [people, query, channel, showRemoved]);

  const roleOptions = useMemo(() => {
    // Every role the shop defines, plus any a member already holds that
    // the roles list did not return (a stale list must not blank a row).
    const names = new Set(roles.filter((role) => !role.is_owner).map((role) => role.role_name));
    for (const row of members) if (row.role && !row.is_owner) names.add(row.role);
    return Array.from(names).sort();
  }, [roles, members]);

  const rowKey = (row: MemberRow) => `${row.chann_uid}:${row.channel}`;

  /** One write, then the list again from the server so the row shows what
   *  it actually became rather than what was asked for. */
  async function act(
    row: MemberRow,
    request: () => Promise<Response>,
    done: string,
  ): Promise<void> {
    setBusyKey(rowKey(row));
    say(t.dashboard.working);
    try {
      const response = await request();
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      await load();
      say(done, "ok");
    } catch {
      say(t.common.error, "error");
    } finally {
      setBusyKey("");
    }
  }

  // Encoded once, outside the URL literal: the dev checks (check-routes,
  // check-parity) read the request URL literals and stop at a "(".
  const uidOf = (row: MemberRow) => encodeURIComponent(row.chann_uid);

  function changeRole(row: MemberRow, role: string) {
    if (!role || role === row.role) return;
    const uid = uidOf(row);
    void act(
      row,
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/members/${uid}/role`, {
          method: "PATCH",
          headers: headers(),
          // `role` + `channel` is the contract; `role_name` is what the
          // route accepted before the channel-aware version shipped, and
          // a server that still reads it ignores the rest.
          body: JSON.stringify({ role, role_name: role, channel: row.channel }),
        }),
      m.roleChanged.replace("{name}", row.display_name).replace("{role}", role),
    );
  }

  function remove(row: MemberRow) {
    if (row.is_owner) {
      say(m.ownerCannotRemove, "error");
      return;
    }
    const question = (row.channel === "technician" ? m.confirmRemoveTechnician : m.confirmRemoveSales)
      .replace("{name}", row.display_name);
    if (!window.confirm(question)) return;
    void setStatusOf(row, "removed", m.removedDone);
  }

  function reactivate(row: MemberRow) {
    const question = m.confirmReactivate
      .replace("{name}", row.display_name)
      .replace("{channel}", channelLabel(row.channel));
    if (!window.confirm(question)) return;
    void setStatusOf(row, "active", m.reactivated);
  }

  function setStatusOf(row: MemberRow, next: MemberStatus, done: string) {
    const uid = uidOf(row);
    return act(
      row,
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/members/${uid}/status`, {
          method: "PATCH",
          headers: headers(),
          body: JSON.stringify({ status: next, channel: row.channel }),
        }),
      done.replace("{name}", row.display_name).replace("{channel}", channelLabel(row.channel)),
    );
  }

  function resetOnboarding(row: MemberRow) {
    const question = m.confirmReset
      .replace("{name}", row.display_name)
      .replace("{channel}", channelLabel(row.channel));
    if (!window.confirm(question)) return;
    const uid = uidOf(row);
    void act(
      row,
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/members/${uid}/reset`, {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ channel: row.channel }),
        }),
      m.resetDone.replace("{name}", row.display_name).replace("{channel}", channelLabel(row.channel)),
    );
  }

  const shownCount = visible.length;
  const inviteHint = m.howToAdd.replace("{command}", m.inviteCommand);

  return (
    <SalesShell
      session={session}
      title={m.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {token && licenseId && (
        <NotificationBell idToken={token} licenseId={licenseId} />
      )}

      <p className="card-meta" style={{ margin: "0 0 12px" }}>
        {m.intro}{" "}
        <Link href="/liff/sales/roles">{m.rolesLink}</Link>
      </p>

      {session.ready && !hasKey && (
        <Empty message={m.readOnly} />
      )}

      {session.ready && hasKey && (
        <>
          <div className="list-controls">
            <label>
              <span>{t.dashboard.search}</span>
              <input
                type="search"
                value={query}
                placeholder={m.search}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label>
              <span>{m.channel}</span>
              <select
                value={channel}
                onChange={(event) => setChannel(event.target.value as "all" | Channel)}
              >
                <option value="all">{m.allChannels}</option>
                <option value="sales">{m.sales}</option>
                <option value="technician">{m.technician}</option>
              </select>
            </label>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 44 }}>
              <input
                type="checkbox"
                checked={showRemoved}
                style={{ width: 20, height: 20 }}
                onChange={(event) => setShowRemoved(event.target.checked)}
              />
              <span style={{ fontSize: 13.5, color: "var(--ink-soft)" }}>{m.showRemoved}</span>
            </label>
          </div>

          {people.length === 0 ? (
            <Empty message={m.empty} action={<p className="card-meta">{inviteHint}</p>} />
          ) : visible.length === 0 ? (
            <Empty message={m.noMatch} />
          ) : (
            <>
              <p className="count">{m.count.replace("{count}", String(shownCount))}</p>
              <div className="list">
                {visible.map((person) => (
                  <article key={person.chann_uid} className="card">
                    <div className="card-title">
                      {person.display_name}
                      {person.isOwner && <Badge stage="won" label={m.owner} />}
                    </div>
                    {person.phone && <p className="card-meta">{person.phone}</p>}

                    {person.rows.map((row) => {
                      const busy = busyKey === rowKey(row);
                      const removed = row.status === "removed";
                      const locked = Boolean(row.is_owner);
                      return (
                        <div
                          key={row.channel}
                          style={{
                            borderTop: "1px solid var(--line)",
                            paddingTop: 10,
                            marginTop: 10,
                            opacity: removed ? 0.75 : 1,
                          }}
                        >
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                            <span className="chip" data-tone={row.channel === "technician" ? "wait" : "live"}>
                              {channelLabel(row.channel)}
                            </span>
                            <Badge stage={removed ? "lost" : "contact"} label={removed ? m.removed : m.active} />
                            {row.joined_at && shortDate(row.joined_at) && (
                              <span className="card-meta" style={{ margin: 0 }}>
                                {m.joined.replace("{date}", shortDate(row.joined_at))}
                              </span>
                            )}
                          </div>

                          <label className="field" style={{ marginTop: 8 }}>
                            <span>{m.role}</span>
                            {locked || !canManage || removed ? (
                              <span style={{ fontSize: 15 }}>{row.role || "—"}</span>
                            ) : (
                              <select
                                value={row.role}
                                disabled={busy}
                                onChange={(event) => changeRole(row, event.target.value)}
                              >
                                {!roleOptions.includes(row.role) && (
                                  <option value={row.role}>{row.role || "—"}</option>
                                )}
                                {roleOptions.map((name) => (
                                  <option key={name} value={name}>{name}</option>
                                ))}
                              </select>
                            )}
                            {locked && canManage && <span className="hint">{m.ownerLocked}</span>}
                            {!locked && canManage && !removed && roleOptions.length === 0 && (
                              <span className="hint">{m.noRoles}</span>
                            )}
                          </label>

                          {canManage && (
                            <div className="card-actions">
                              {removed ? (
                                <button
                                  type="button"
                                  className="btn"
                                  data-variant="primary"
                                  disabled={busy}
                                  onClick={() => reactivate(row)}
                                >
                                  {m.reactivate}
                                </button>
                              ) : (
                                !locked && (
                                  <button
                                    type="button"
                                    className="btn"
                                    data-variant="quiet"
                                    disabled={busy}
                                    onClick={() => remove(row)}
                                  >
                                    {m.remove}
                                  </button>
                                )
                              )}
                              <button
                                type="button"
                                className="btn"
                                data-variant="quiet"
                                disabled={busy}
                                onClick={() => resetOnboarding(row)}
                              >
                                {m.reset}
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </article>
                ))}
              </div>
              <p className="card-meta" style={{ marginTop: 16 }}>{inviteHint}</p>
            </>
          )}
        </>
      )}
    </SalesShell>
  );
}

/** `{items: [...]}` or a bare array, with the fields the page reads
 *  defaulted: a server that predates `channel` is a sales-only list. */
function normalise(body: unknown): MemberRow[] {
  const items = Array.isArray(body)
    ? body
    : body && typeof body === "object" && Array.isArray((body as { items?: unknown }).items)
      ? ((body as { items: unknown[] }).items)
      : [];
  return items
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item): MemberRow => ({
      chann_uid: String(item.chann_uid ?? ""),
      display_name: String(item.display_name ?? item.chann_uid ?? ""),
      role: String(item.role ?? ""),
      channel: item.channel === "technician" ? "technician" : "sales",
      status: item.status === "removed" ? "removed" : "active",
      joined_at: typeof item.joined_at === "string" ? item.joined_at : null,
      is_owner: Boolean(item.is_owner),
      phone: typeof item.phone === "string" ? item.phone : null,
    }))
    .filter((row) => row.chann_uid);
}
