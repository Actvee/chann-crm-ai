"use client";

import { useCallback, useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import type { PlanInfo, SalesContact } from "../_plan";
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
  /** The EFFECTIVE keys: the role's keys minus what the plan locks (round
   *  21D) — so every existing `permissions.has(...)` is plan-aware. */
  permissions: Set<string>;
  /** Round 21D (ruling R-C): the role's keys BEFORE the plan. The nav
   *  tells "no permission" (hidden) from "plan-locked" (shown locked)
   *  with this; held = permissions ∪ plan-locked. */
  heldKeys: Set<string>;
  /** Round 21D: the shop's plan (null until /me answers — then nothing is
   *  drawn as locked; the server refuses anyway). */
  plan: PlanInfo | null;
  /** The upgrade contact, for whoever can act on it (null otherwise). */
  salesContact: SalesContact;
  /** /me answered 200. Only then is an empty key set an answer ("nothing")
   *  rather than "unknown" (review I1). */
  meAnswered: boolean;
  isOwner: boolean;
  channUid: string;
  /** Phase 18: a suspended shop is read-only; every write is refused with 423. */
  suspended: boolean;
  /** Round 19b: the shop's status and when it expires — the company page says so. */
  licenseStatus: string;
  licenseExpiresAt: string | null;
  /** True once token, shop and permissions are all known. */
  ready: boolean;
  /** Several shops and none of them chosen: the page must ask rather than
   *  open one of them (round 19n). /me answers with the chosen shop first,
   *  so memberships[0] looks like an answer even when nobody decided. */
  mustChooseShop: boolean;
};

const EMPTY: SalesSession = {
  token: "",
  memberships: [],
  licenseId: "",
  permissions: new Set(),
  heldKeys: new Set(),
  plan: null,
  salesContact: null,
  meAnswered: false,
  isOwner: false,
  channUid: "",
  suspended: false,
  licenseStatus: "active",
  licenseExpiresAt: null,
  ready: false,
  mustChooseShop: false,
};

type Say = (message: string, kind?: "ok" | "error") => void;

type Me = {
  keys: string[]; heldKeys: string[]; isOwner: boolean; channUid: string; licenseStatus: string;
  plan: PlanInfo | null; salesContact: SalesContact; answered: boolean;
};

const NO_ME: Me = {
  keys: [], heldKeys: [], isOwner: false, channUid: "", licenseStatus: "active",
  plan: null, salesContact: null, answered: false,
};

async function fetchMe(token: string, licenseId: string): Promise<Me> {
  try {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/me/permissions`,
      { headers: proxyHeaders(token, licenseId) },
    );
    if (!response.ok) return NO_ME;
    const body = (await response.json()) as {
      permission_keys?: string[]; held_keys?: string[]; is_owner?: boolean; chann_uid?: string;
      license_status?: string; plan?: PlanInfo | null; sales_contact?: SalesContact;
    };
    const keys = body.permission_keys ?? [];
    return {
      keys,
      // An Application image older than round 21D sends no held_keys:
      // nothing is plan-locked there, so held == effective.
      heldKeys: body.held_keys ?? keys,
      isOwner: Boolean(body.is_owner),
      channUid: body.chann_uid ?? "",
      licenseStatus: body.license_status ?? "active",
      plan: body.plan ?? null,
      salesContact: body.sales_contact ?? null,
      answered: true,
    };
  } catch {
    // An empty set means "show everything read-only", which is the safe
    // direction to fail in: nothing is offered that would then be refused.
    return NO_ME;
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
      const licenseId = started.activeLicenseId || (started.memberships[0]?.license_id ?? "");
      if (!licenseId) {
        setSession({ ...EMPTY, token: started.token, memberships: started.memberships });
        sayRef.current(tRef.current.liff.noCompany, "error");
        return;
      }
      if (started.memberships.length > 1 && !started.activeLicenseId) {
        // Ask, and load nothing until they say. Chat has asked since
        // 3 ก.ย.; the screens picked the first row instead.
        setSession({
          ...EMPTY, token: started.token, memberships: started.memberships,
          mustChooseShop: true,
        });
        sayRef.current(tRef.current.liff.chooseShop, undefined);
        return;
      }
      const me = await fetchMe(started.token, licenseId);
      // Suspended (Phase 18) and soft-deleted (round 18) are both read-only.
      const readOnly = new Set(["suspended", "deleted"]);
      const suspended =
        readOnly.has(me.licenseStatus ?? "") ||
        readOnly.has(started.memberships[0]?.license_status ?? "");
      setSession({
        token: started.token,
        memberships: started.memberships,
        licenseId,
        permissions: new Set(me.keys),
        heldKeys: new Set(me.heldKeys),
        plan: me.plan,
        salesContact: me.salesContact,
        meAnswered: me.answered,
        isOwner: me.isOwner,
        channUid: me.channUid,
        suspended,
        licenseStatus: me.licenseStatus || started.memberships[0]?.license_status || "active",
        licenseExpiresAt: started.memberships[0]?.license_expires_at ?? null,
        ready: true,
        mustChooseShop: false,
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
