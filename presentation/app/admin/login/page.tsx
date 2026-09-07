"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

const copy = ADMIN.login;

/** "14:35" in the operator's own clock, from the ISO the Data tier sent. */
function clockOf(iso: unknown): string {
  if (typeof iso !== "string") return "";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Phase 18 — the operator signs in with a username and password; the
 *  session lives in an httpOnly cookie set by /api/admin/login. */
export default function AdminLogin() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Sent back here by a console page whose session had ended (D13):
    // say so, rather than presenting a login form with no explanation.
    if (new URLSearchParams(window.location.search).get("expired")) {
      setMessage(copy.sessionExpired);
    }
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setMessage("");
    setBusy(true);
    try {
      const res = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        router.push("/admin");
        router.refresh();
      } else if (res.status === 423) {
        // Locked after five wrong passwords (D2): retrying does nothing
        // until the time the Data tier named.
        const body = (await res.json().catch(() => null)) as { detail?: { locked_until?: unknown } } | null;
        const until = clockOf(body?.detail?.locked_until);
        setMessage(until ? copy.locked(until) : copy.lockedUnknown);
      } else {
        setMessage(res.status === 401 ? copy.invalid : copy.unavailable);
      }
    } catch {
      setMessage(copy.unavailable);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pa-card pa-login-card">
      <span className="pa-brand-mark" aria-hidden="true">C</span>
      <h1>{copy.title}</h1>
      <p>{copy.intro}</p>
      <form onSubmit={submit}>
        <label className="pa-field">
          {copy.username}
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className="pa-field">
          {copy.password}
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button type="submit" className="pa-btn pa-btn-primary" disabled={busy}>
          {busy ? copy.working : copy.submit}
        </button>
        {message && <p className="pa-note pa-note-error" role="alert">{message}</p>}
      </form>
    </div>
  );
}
