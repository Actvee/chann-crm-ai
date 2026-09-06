"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../_components";
import { FieldRow } from "../../_field-row";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

type Team = { id: string; team_name: string };
type Technician = { id: string; chann_uid: string; display_name: string; phone?: string | null };
type TeamMember = Technician & { is_lead?: boolean };

/**
 * Technician teams (Phase 7 organisation, Phase 12 dispatch).
 *
 * The Data Tier has had teams and leads since Phase 7; nothing above it
 * let a shop form one, so "มอบหมาย T-… ให้ทีม แอร์" had no team to name
 * (owner audit, 3 Sep). Chat does the same things with the same routes:
 * "สร้างทีมช่าง แอร์", "เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า".
 */
export default function SalesTeams({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const copy = t.dashboard.teams;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [teams, setTeams] = useState<Team[]>([]);
  const [members, setMembers] = useState<Record<string, TeamMember[]>>({});
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [newTeam, setNewTeam] = useState("");
  const [picked, setPicked] = useState<Record<string, string>>({});

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const headers = proxyHeaders(currentToken, license);
      const [teamsRes, techRes] = await Promise.all([
        fetch(`/api/phase2/licenses/${license}/technician-teams`, { headers }),
        fetch(`/api/phase2/licenses/${license}/technicians`, { headers }),
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
            `/api/phase2/licenses/${license}/technician-teams/${team.id}/members`,
            { headers },
          );
          byTeam[team.id] = res.ok ? ((await res.json()) as TeamMember[]) : [];
        }),
      );
      setMembers(byTeam);
    },
    [token, licenseId, t],
  );

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setToken(session.token);
      setLicenseId(license);
      const permissions = await fetchPermissions(session.token, license);
      setCanManage(permissions.has("team.manage"));
      await load(session.token, license);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  /** One request, then reload; every caller names its own URL so the
   *  route checker (and a reader) can see exactly what the page calls. */
  async function send(request: () => Promise<Response>) {
    setBusy(true);
    try {
      const response = await request();
      if (!response.ok) throw new Error(String(response.status));
      await load();
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

  const deleteTeam = (teamId: string) =>
    window.confirm(copy.confirmDeleteTeam) &&
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

  const removeMember = (teamId: string, memberId: string) =>
    window.confirm(copy.confirmRemove) &&
    send(() =>
      fetch(`/api/phase2/licenses/${licenseId}/technician-teams/${teamId}/members/${memberId}`, {
        method: "DELETE",
        headers: headers(),
      }),
    );

  const unassigned = (teamId: string) => {
    const inTeam = new Set((members[teamId] ?? []).map((m) => m.id));
    return technicians.filter((tech) => !inTeam.has(tech.id));
  };

  return (
    <AppShell
      title={copy.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="page-intro">{copy.intro}</p>

      {canManage && (
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

      {teams.length === 0 ? (
        <div className="empty">
          <p>{copy.empty}</p>
        </div>
      ) : (
        teams.map((team) => (
          <section key={team.id} className="section">
            <div className="section-head">
              <h2>{team.team_name}</h2>
              {canManage && (
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  disabled={busy}
                  onClick={() => void deleteTeam(team.id)}
                >
                  {copy.deleteTeam}
                </button>
              )}
            </div>
            {(members[team.id] ?? []).length === 0 ? (
              <div className="empty">
                <p>{copy.noMembers}</p>
              </div>
            ) : (
              <ul className="list">
                {(members[team.id] ?? []).map((member) => (
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
                          onClick={() => void removeMember(team.id, member.id)}
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
    </AppShell>
  );
}
