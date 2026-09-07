"use client";

import { useCallback, useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Membership, initLiffSession, proxyHeaders } from "../_shared";

/**
 * One session for every Sales page.
 *
 * Review C5/C7 (6 Sep 2026): five list pages carried a CompanyPicker that
 * changed `licenseId` locally, every detail page read `memberships[0]`,
 * and the quote page never asked for permissions at all. The server
 * already keeps the chosen shop per OA (the same store chat's "ใช้ร้าน X"
 * writes) and answers /me with that shop first — so the active shop is
 * always `memberships[0]`, a switch is "tell the server, then start
 * again", and the permissions come with the session rather than being
 * something a page remembers to fetch.
 */
export type SalesSession = {
  token: string;
  memberships: Membership[];
  /** The active shop — memberships[0] once the server has been asked. */
  licenseId: string;
  permissions: Set<string>;
  isOwner: boolean;
  channUid: string;
  /** Phase 18: a suspended shop is read-only; every write is refused with 423. */
  suspended: boolean;
  /** True once token, shop and permissions are all known. */
  ready: boolean;
};

const EMPTY: SalesSession = {
  token: "",
  memberships: [],
  licenseId: "",
  permissions: new Set(),
  isOwner: false,
  channUid: "",
  suspended: false,
  ready: false,
};

type Say = (message: string, kind?: "ok" | "error") => void;

async function fetchMe(token: string, licenseId: string): Promise<{
  keys: string[]; isOwner: boolean; channUid: string; licenseStatus: string;
}> {
  try {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/me/permissions`,
      { headers: proxyHeaders(token, licenseId) },
    );
    if (!response.ok) return { keys: [], isOwner: false, channUid: "", licenseStatus: "active" };
    const body = (await response.json()) as {
      permission_keys?: string[]; is_owner?: boolean; chann_uid?: string; license_status?: string;
    };
    return {
      keys: body.permission_keys ?? [],
      isOwner: Boolean(body.is_owner),
      channUid: body.chann_uid ?? "",
      licenseStatus: body.license_status ?? "active",
    };
  } catch {
    // An empty set means "show everything read-only", which is the safe
    // direction to fail in: nothing is offered that would then be refused.
    return { keys: [], isOwner: false, channUid: "", licenseStatus: "active" };
  }
}

export function useSalesSession(liffId: string, say: Say) {
  const { t } = useLanguage();
  const [session, setSession] = useState<SalesSession>(EMPTY);
  // The latest `say` and dictionary, without making initialize depend on
  // them: a dependency there restarts the LIFF handshake on every render.
  const sayRef = useRef(say);
  sayRef.current = say;
  const tRef = useRef(t);
  tRef.current = t;

  const initialize = useCallback(async () => {
    try {
      const started = await initLiffSession(liffId);
      if (!started.token) return; // login redirect in progress
      const licenseId = started.memberships[0]?.license_id ?? "";
      if (!licenseId) {
        setSession({ ...EMPTY, token: started.token, memberships: started.memberships });
        sayRef.current(tRef.current.liff.noCompany, "error");
        return;
      }
      const me = await fetchMe(started.token, licenseId);
      const suspended =
        me.licenseStatus === "suspended" ||
        started.memberships[0]?.license_status === "suspended";
      setSession({
        token: started.token,
        memberships: started.memberships,
        licenseId,
        permissions: new Set(me.keys),
        isOwner: me.isOwner,
        channUid: me.channUid,
        suspended,
        ready: true,
      });
    } catch (error) {
      sayRef.current(
        error instanceof Error ? error.message : tRef.current.dashboard.openFailed,
        "error",
      );
    }
  }, [liffId]);

  /** After ShopSwitcher has told the server: start again from /me, so
   *  the new shop, its permissions and its data all come from one place. */
  const switchShop = useCallback(async () => {
    setSession((current) => ({ ...current, ready: false }));
    sayRef.current(tRef.current.dashboard.opening);
    await initialize();
    sayRef.current(tRef.current.dashboard.customer.shopSwitched, "ok");
  }, [initialize]);

  return { ...session, initialize, switchShop };
}

export type SalesSessionHandle = ReturnType<typeof useSalesSession>;
