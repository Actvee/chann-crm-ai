"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { FieldRow } from "../../_field-row";
import { ListFilters, matchesQuery } from "../../_filters";
import { PlanLocked, planHas } from "../../_plan";
import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";
import { ConfirmDialog, useConfirm } from "../../_confirm";

type Team = { id: string; team_name: string };
type Technician = { id: string; chann_uid: string; display_name: string; phone?: string | null };
type TeamMember = Technician & { is_lead?: boolean };
type Group = { id: string; group_name: string };
type Member = Technician & { role?: string };

/**
 * Technician teams (Phase 7 organisation, Phase 12 dispatch) — and, since
 * the 6 Sep 2026 review (E7), sales groups.
 *
 * The Data Tier has had teams and leads since Phase 7; nothing above it
 * let a shop form one, so "มอบหมาย T-… ให้ทีม แอร์" had no team to name
 * (owner audit, 3 Sep). Chat does the same things with the same routes:
 * "สร้างทีมช่าง แอร์", "เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า".
 *
 * Sales groups had the same shape of gap one step further along: chat
 * could create one and nothing anywhere could put a person in it.
 */
export default function SalesTeams({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const s = useSalesText();
  const failureText = useFailureText();
  const copy = t.dashboard.teams;

  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [teams, setTeams] = useState<Team[]>([]);
  const [members, setMembers] = useState<Record<string, TeamMember[]>>({});
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [groupMembers, setGroupMembers] = useState<Record<string, Member[]>>({});
  const [everyone, setEveryone] = useState<Member[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [newTeam, setNewTeam] = useState("");
  const [newGroup, setNewGroup] = useState("");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [pickedForGroup, setPickedForGroup] = useState<Record<string, string>>({});

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;
  const canManage = !session.suspended && permissions.has("team.manage");
  // Round 21D (ruling 25): technician teams are the service feature; sales
  // groups are on every plan. On a plan without service only the technician
  // part is locked (and not asked for — the API refuses it there), so a
  // Starter shop still runs its sales groups here.
  const serviceOn = planHas(session.plan, "feature.service");

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const headers = proxyHeaders(token, licenseId);
    const [teamsRes, techRes] = await Promise.all([
      fetch(`/api/phase2/licenses/${licenseId}/technician-teams`, { headers }),
      fetch(`/api/phase2/licenses/${licenseId}/technicians`, { headers }),
    ]);
    if (!teamsRes.ok) {
      throw new Error(
        teamsRes.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${teamsRes.status})`,
      );
    }
    const rows = (await teamsRes.json()) as Team[];
    setTeams(rows);
    setTechnicians(techRes.ok ? ((await techRes.json()) as Technician[]) : []);
    const byTeam: Record<string, TeamMember[]> = {};
    await Promise.all(
      rows.map(async (team) => {
        const res = await fetch(
          `/api/phase2/licenses/${licenseId}/technician-teams/${team.id}/members`,
          { headers },
        );
        byTeam[team.id] = res.ok ? ((await res.json()) as TeamMember[]) : [];
      }),
    );
    setMembers(byTeam);
  }, [token, licenseId, t]);

  // Sales groups are team.manage all the way down (the key chat uses for
  // "สร้างกลุ่มเซลส์"), so someone without it sees the teams and not this.
  const loadGroups = useCallback(async () => {
    if (!token || !licenseId || !permissions.has("team.manage")) return;
    const headers = proxyHeaders(token, licenseId);
    const [groupsRes, membersRes] = await Promise.all([
      fetch(`/api/phase2/licenses/${licenseId}/sales-groups`, { headers }),
      fetch(`/api/phase2/licenses/${licenseId}/members`, { headers }),
    ]);
    if (!groupsRes.ok) return;
    const rows = (await groupsRes.json()) as Group[];
    setGroups(rows);
    setEveryone(membersRes.ok ? ((await membersRes.json()) as Member[]) : []);
    const byGroup: Record<string, Member[]> = {};
    await Promise.all(
      rows.map(async (group) => {
        const res = await fetch(
          `/api/phase2/licenses/${licenseId}/sales-groups/${group.id}/members`,
          { headers },
        );
        byGroup[group.id] = res.ok ? ((await res.json()) as Member[]) : [];
      }),
    );
    setGroupMembers(byGroup);
  }, [licenseId, permissions, token]);

  useEffect(() => {
    if (!session.ready) return;
    void (async () => {
      // The two halves load independently: a refused technician list must
      // not take the sales groups down with it.
      let failure = "";
      if (serviceOn) {
        try {
          await load();
        } catch (error) {
          failure = error instanceof Error ? error.message : t.dashboard.loadFailed;
        }
      }
      try {
        await loadGroups();
      } catch (error) {
        failure = failure || (error instanceof Error ? error.message : t.dashboard.loadFailed);
      }
      say(failure, failure ? "error" : undefined);
    })();
  }, [session.ready, serviceOn, load, loadGroups, say, t]);

  /** One request, then reload; every caller names its own URL so the
   *  route checker (and a reader) can see exactly what the page calls. */
  async function send(request: () => Promise<Response>, reload: () => Promise<void> = load) {
    setBusy(true);
    try {
      const response = await request();
      if (!response.ok) {
        say(await failureText(response), "error");
        return false;
      }
      await reload();
      return true;
    } catch {
      say(copy.actionFailed, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const headers = () => proxyHeaders(token, licenseId);

  async function createTeam() {
    if (!newTeam.trim()) return;
    const ok = await send(() =>
      fetch(`/api/phase2/licenses/${licenseId}/technician-teams`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ team_name: newTeam.trim() }),
      }),
    );
    if (ok) {
      setNewTeam("");
      say(copy.created, "ok");
    }
  }

  const deleteTeam = async (teamId: string, teamName: string) =>
    (await ask({
      action: t.common.delete,
      target: teamName,
      affects: [copy.deleteTeamAffects],
      permanent: true,
      confirmLabel: copy.deleteTeamButton,
    })) &&
    send(() =>
      fetch(`/api/phase2/licenses/${licenseId}/technician-teams/${teamId}`, {
        method: "DELETE",
        headers: headers(),
      }),
    );

  const setMember = (teamId: string, memberId: string, isLead?: boolean) =>
    send(() =>
      fetch(`/api/phase2/licenses/${licenseId}/technician-teams/${teamId}/members`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ member_id: memberId, ...(isLead === undefined ? {} : { is_lead: isLead }) }),
      }),
    );

  const removeMember = async (teamId: string, memberId: string, memberName: string) =>
    (await ask({
      action: copy.removeMemberAction,
      target: memberName,
      affects: [copy.removeMemberAffects],
      reversible: copy.removeMemberKeeps,
      confirmLabel: copy.removeMemberAction,
    })) &&
    send(() =>
      fetch(`/api/phase2/licenses/${licenseId}/technician-teams/${teamId}/members/${memberId}`, {
        method: "DELETE",
        headers: headers(),
      }),
    );

  async function createGroup() {
    if (!newGroup.trim()) return;
    const ok = await send(
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/sales-groups`, {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ group_name: newGroup.trim() }),
        }),
      loadGroups,
    );
    if (ok) {
      setNewGroup("");
      say(s.groups.created, "ok");
    }
  }

  const deleteGroup = async (groupId: string, groupName: string) =>
    (await ask({
      action: t.common.delete,
      target: groupName,
      affects: [s.groups.deleteAffects],
      permanent: true,
      confirmLabel: s.groups.deleteButton,
    })) &&
    send(
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/sales-groups/${groupId}`, {
          method: "DELETE",
          headers: headers(),
        }),
      loadGroups,
    );

  const addToGroup = (groupId: string, memberId: string) =>
    send(
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/sales-groups/${groupId}/members`, {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ member_id: memberId }),
        }),
      loadGroups,
    );

  const removeFromGroup = async (groupId: string, memberId: string, memberName: string) =>
    (await ask({
      action: s.groups.removeAction,
      target: memberName,
      reversible: s.groups.removeKeeps,
      confirmLabel: s.groups.removeAction,
    })) &&
    send(
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/sales-groups/${groupId}/members/${memberId}`, {
          method: "DELETE",
          headers: headers(),
        }),
      loadGroups,
    );

  // A team (or group) shows whole when its name matches; otherwise it
  // shows narrowed to the members who do, so a person is found by name,
  // phone or role wherever they sit.
  const nameMatches = (name: string) => matchesQuery(query, [name]);
  const memberMatches = (member: Technician & { role?: string }) =>
    matchesQuery(query, [member.display_name, member.phone, member.role]);
  const visibleTeams = teams.flatMap((team) => {
    const all = members[team.id] ?? [];
    const rows = nameMatches(team.team_name) ? all : all.filter(memberMatches);
    return rows.length > 0 || nameMatches(team.team_name) ? [{ team, rows }] : [];
  });
  const visibleGroups = groups.flatMap((group) => {
    const all = groupMembers[group.id] ?? [];
    const rows = nameMatches(group.group_name) ? all : all.filter(memberMatches);
    return rows.length > 0 || nameMatches(group.group_name) ? [{ group, rows }] : [];
  });

  const unassigned = (teamId: string) => {
    const inTeam = new Set((members[teamId] ?? []).map((m) => m.id));
    return technicians.filter((tech) => !inTeam.has(tech.id));
  };
  const notInGroup = (groupId: string) => {
    const inGroup = new Set((groupMembers[groupId] ?? []).map((m) => m.id));
    return everyone.filter((m) => !inGroup.has(m.id));
  };

  return (
    <SalesShell
      session={session}
      title={copy.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="page-intro">{copy.intro}</p>

      <ListFilters query={query} onQuery={setQuery} />

      {session.ready && !serviceOn && (
        <PlanLocked
          feature="feature.service"
          plan={session.plan}
          canUpgrade={session.isOwner || permissions.has("setting.manage")}
          contact={session.salesContact}
        />
      )}

      {serviceOn && canManage && (
        <section className="section">
          <div className="section-head">
            <h2>{copy.create}</h2>
          </div>
          <dl className="fields">
            <FieldRow label={copy.newTeam}>
              {(id) => (
                <input id={id} value={newTeam} onChange={(e) => setNewTeam(e.target.value)} />
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled={busy || !newTeam.trim()}
                onClick={() => void createTeam()}
              >
                {busy ? t.dashboard.related.saving : copy.create}
              </button>
            </div>
          </dl>
        </section>
      )}

      {!session.ready || !serviceOn ? null : teams.length === 0 ? (
        <div className="empty">
          <p>{copy.empty}</p>
        </div>
      ) : visibleTeams.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.noMatch}</p>
        </div>
      ) : (
        visibleTeams.map(({ team, rows }) => (
          <section key={team.id} className="section">
            <div className="section-head">
              <h2>{team.team_name}</h2>
              {canManage && (
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  disabled={busy}
                  onClick={() => void deleteTeam(team.id, team.team_name)}
                >
                  {copy.deleteTeam}
                </button>
              )}
            </div>
            {rows.length === 0 ? (
              <div className="empty">
                <p>{copy.noMembers}</p>
              </div>
            ) : (
              <ul className="list">
                {rows.map((member) => (
                  <li key={member.id} className="card">
                    <div className="card-title">
                      {member.display_name}
                      {member.is_lead && (
                        <span className="badge" data-tone="ok" style={{ marginLeft: 8 }}>
                          {copy.lead}
                        </span>
                      )}
                    </div>
                    {member.phone && <div className="card-meta">{member.phone}</div>}
                    {canManage && (
                      <div className="card-actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          disabled={busy}
                          onClick={() => void setMember(team.id, member.id, !member.is_lead)}
                        >
                          {member.is_lead ? copy.unmakeLead : copy.makeLead}
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          disabled={busy}
                          onClick={() => void removeMember(team.id, member.id, member.display_name)}
                        >
                          {copy.remove}
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {canManage && (
              <dl className="fields" style={{ marginTop: 12 }}>
                <FieldRow label={copy.addMember}>
                  {(id) =>
                    technicians.length === 0 ? (
                      <span className="hint">{copy.noTechnicians}</span>
                    ) : (
                      <select
                        id={id}
                        value={picked[team.id] ?? ""}
                        onChange={(e) => setPicked({ ...picked, [team.id]: e.target.value })}
                      >
                        <option value="">{copy.pickTechnician}</option>
                        {unassigned(team.id).map((tech) => (
                          <option key={tech.id} value={tech.id}>
                            {tech.display_name}
                          </option>
                        ))}
                      </select>
                    )
                  }
                </FieldRow>
                <div className="actions">
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    disabled={busy || !picked[team.id]}
                    onClick={() =>
                      void setMember(team.id, picked[team.id]).then(
                        (ok) => ok && setPicked({ ...picked, [team.id]: "" }),
                      )
                    }
                  >
                    {copy.addMember}
                  </button>
                </div>
              </dl>
            )}
          </section>
        ))
      )}

      {canManage && (
        <>
          <h2 style={{ marginTop: 28 }}>{s.groups.title}</h2>
          <p className="page-intro">{s.groups.intro}</p>

          <section className="section">
            <div className="section-head">
              <h2>{s.groups.create}</h2>
            </div>
            <dl className="fields">
              <FieldRow label={s.groups.newGroup}>
                {(id) => (
                  <input id={id} value={newGroup} onChange={(e) => setNewGroup(e.target.value)} />
                )}
              </FieldRow>
              <div className="actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="primary"
                  disabled={busy || !newGroup.trim()}
                  onClick={() => void createGroup()}
                >
                  {busy ? t.dashboard.related.saving : s.groups.create}
                </button>
              </div>
            </dl>
          </section>

          {groups.length === 0 ? (
            <div className="empty">
              <p>{s.groups.empty}</p>
            </div>
          ) : visibleGroups.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.noMatch}</p>
            </div>
          ) : (
            visibleGroups.map(({ group, rows }) => (
              <section key={group.id} className="section">
                <div className="section-head">
                  <h2>{group.group_name}</h2>
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    disabled={busy}
                    onClick={() => void deleteGroup(group.id, group.group_name)}
                  >
                    {s.groups.deleteGroup}
                  </button>
                </div>
                {rows.length === 0 ? (
                  <div className="empty">
                    <p>{copy.noMembers}</p>
                  </div>
                ) : (
                  <ul className="list">
                    {rows.map((member) => (
                      <li key={member.id} className="card">
                        <div className="card-title">{member.display_name}</div>
                        <div className="card-meta">
                          {member.role}
                          {member.phone ? ` · ${member.phone}` : ""}
                        </div>
                        <div className="card-actions">
                          <button
                            type="button"
                            className="btn"
                            data-variant="quiet"
                            disabled={busy}
                            onClick={() => void removeFromGroup(group.id, member.id, member.display_name)}
                          >
                            {copy.remove}
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                <dl className="fields" style={{ marginTop: 12 }}>
                  <FieldRow label={s.groups.addMember}>
                    {(id) =>
                      notInGroup(group.id).length === 0 ? (
                        <span className="hint">{s.groups.allIn}</span>
                      ) : (
                        <select
                          id={id}
                          value={pickedForGroup[group.id] ?? ""}
                          onChange={(e) =>
                            setPickedForGroup({ ...pickedForGroup, [group.id]: e.target.value })
                          }
                        >
                          <option value="">{s.groups.pickMember}</option>
                          {notInGroup(group.id).map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.display_name}
                              {m.role ? ` (${m.role})` : ""}
                            </option>
                          ))}
                        </select>
                      )
                    }
                  </FieldRow>
                  <div className="actions">
                    <button
                      type="button"
                      className="btn"
                      data-variant="primary"
                      disabled={busy || !pickedForGroup[group.id]}
                      onClick={() =>
                        void addToGroup(group.id, pickedForGroup[group.id]).then(
                          (ok) => ok && setPickedForGroup({ ...pickedForGroup, [group.id]: "" }),
                        )
                      }
                    >
                      {s.groups.addMember}
                    </button>
                  </div>
                </dl>
              </section>
            ))
          )}
        </>
      )}
      <ConfirmDialog request={confirming} onClose={closeConfirm} />
    </SalesShell>
  );
}
