"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import { applyNavCollapsed, readNavCollapsed, writeNavCollapsed } from "@/lib/nav-state";

import { ICONS, currentKey, guideEntry, mayOpen, navGroups, type NavEntry } from "./_nav-model";
import type { Audience } from "./_shared";

/**
 * The dashboard's left navigation (owner, 8 Sep 2026: "รูปแบบเมนูควรเป็น
 * navigation อยู่ด้านซ้าย เปิด-ยุบได้").
 *
 * One component, three shapes:
 *   · phone — an off-canvas drawer behind a menu button, because LINE's
 *     in-app browser is the surface this is used on and a permanent rail
 *     would eat a third of a 360px screen;
 *   · tablet and desktop — a permanent rail, expanded (icon + label);
 *   · the same rail collapsed to icons, remembered per person.
 *
 * The collapsed choice is applied by an inline script in the root layout
 * before first paint (`html[data-nav="collapsed"]`), so the rail is never
 * drawn wide and then snapped shut. React adopts the same value in an
 * effect afterwards — reading storage during render would make the server
 * and client markup disagree, which is the trap LanguageProvider already
 * documents.
 */

/** Where the rail stops being a drawer and becomes furniture. Matches the
 *  `min-width: 900px` block in globals.css; the two must stay in step. */
const RAIL_QUERY = "(min-width: 900px)";

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

type NavContextValue = {
  enabled: boolean;
  open: boolean;
  railId: string;
  openDrawer: () => void;
  buttonRef: { current: HTMLButtonElement | null };
};

const NavContext = createContext<NavContextValue | null>(null);

/** True while the viewport is wide enough for the permanent rail. Starts
 *  false on the server and on the first client render, which is the phone
 *  case — the one this product is mostly used on. */
function useWideViewport(): boolean {
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(RAIL_QUERY);
    const sync = () => setWide(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);
  return wide;
}

/**
 * Wraps a dashboard page: the rail on the left, the page's own shell on
 * the right. `enabled={false}` renders the children alone, for a surface
 * that has no menu of its own.
 */
export function NavFrame({
  audience,
  permissions,
  isOwner = false,
  enabled = true,
  children,
}: {
  audience: Audience;
  permissions?: Set<string>;
  isOwner?: boolean;
  enabled?: boolean;
  children: ReactNode;
}) {
  const { t } = useLanguage();
  const pathname = usePathname() ?? "";
  const wide = useWideViewport();
  const railId = useId();
  const railRef = useRef<HTMLElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  const [collapsed, setCollapsed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setCollapsed(readNavCollapsed());
  }, []);

  // A drawer left open behind a widened window would sit under the rail
  // it became, with the page still scroll-locked.
  useEffect(() => {
    if (wide) setOpen(false);
  }, [wide]);

  const closeDrawer = useCallback(
    (returnFocus: boolean) => {
      setOpen(false);
      // Only when the person dismissed it. Following a link moves them to
      // another page, and pulling focus back to a menu button there would
      // undo the navigation they just made.
      if (returnFocus) buttonRef.current?.focus();
    },
    [],
  );

  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      writeNavCollapsed(next);
      applyNavCollapsed(next);
      return next;
    });
  }, []);

  // Escape, the focus trap, and the scroll lock — only while the drawer is
  // the shape the rail is in.
  useEffect(() => {
    if (!open) return;
    const rail = railRef.current;
    if (!rail) return;

    // Only what is actually on screen: the collapse control is display:
    // none in the drawer shape, and focusing a hidden button does nothing
    // at all — which is how a "trap" ends up letting focus out.
    const reachable = () =>
      Array.from(rail.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null,
      );

    const body = document.body;
    const previousOverflow = body.style.overflow;
    body.style.overflow = "hidden";
    reachable()[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDrawer(true);
        return;
      }
      if (event.key !== "Tab") return;
      const items = reachable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && rail.contains(active);
      if (event.shiftKey && (!inside || active === first)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (!inside || active === last)) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      // Restore rather than clear: another component may have set it.
      body.style.overflow = previousOverflow;
    };
  }, [open, closeDrawer]);

  const groups = useMemo(() => {
    const perms = permissions ?? new Set<string>();
    return navGroups(t, audience)
      .map((group) => ({
        ...group,
        entries: group.entries.filter((entry) => mayOpen(entry, perms, isOwner)),
      }))
      .filter((group) => group.entries.length > 0);
  }, [t, audience, permissions, isOwner]);

  const guide = useMemo(() => guideEntry(t, audience), [t, audience]);

  const active = useMemo(
    () => currentKey([...groups.flatMap((group) => group.entries), guide], pathname),
    [groups, guide, pathname],
  );

  const context = useMemo<NavContextValue>(
    () => ({ enabled, open, railId, openDrawer: () => setOpen(true), buttonRef }),
    [enabled, open, railId],
  );

  if (!enabled) {
    return <NavContext.Provider value={context}>{children}</NavContext.Provider>;
  }

  const nav = t.dashboard.nav;

  return (
    <NavContext.Provider value={context}>
      <div className="app" data-drawer={open ? "open" : undefined}>
        {/* Dismisses the drawer on a tap outside it. A button rather than a
            div so it is a real control; it is out of the tab order because
            Escape and the close button are the keyboard route out. */}
        <button
          type="button"
          className="rail-scrim"
          tabIndex={-1}
          aria-hidden="true"
          onClick={() => closeDrawer(true)}
        />
        <nav
          id={railId}
          ref={railRef}
          className="rail"
          aria-label={nav.label}
        >
          <div className="rail-head">
            <button
              type="button"
              className="rail-btn rail-collapse"
              aria-expanded={!collapsed}
              aria-controls={railId}
              onClick={toggleCollapsed}
              title={collapsed ? nav.expand : nav.collapse}
            >
              <span className="rail-btn-icon">{collapsed ? ICONS.expand : ICONS.collapse}</span>
              <span className="sr-only">{collapsed ? nav.expand : nav.collapse}</span>
            </button>
            <button
              type="button"
              className="rail-btn rail-close"
              onClick={() => closeDrawer(true)}
              title={nav.close}
            >
              <span className="rail-btn-icon">{ICONS.close}</span>
              <span className="sr-only">{nav.close}</span>
            </button>
          </div>

          {groups.map((group) => (
            <div className="rail-group" key={group.key}>
              {group.label && (
                <p className="rail-group-label">
                  <span>{group.label}</span>
                </p>
              )}
              <ul>
                {group.entries.map((entry) => (
                  <li key={entry.key}>
                    <RailLink entry={entry} active={active === entry.key} onNavigate={() => closeDrawer(false)} />
                  </li>
                ))}
              </ul>
            </div>
          ))}

          <div className="rail-foot">
            <RailLink entry={guide} active={active === guide.key} onNavigate={() => closeDrawer(false)} />
          </div>
        </nav>
        {children}
      </div>
    </NavContext.Provider>
  );
}

function RailLink({
  entry,
  active,
  onNavigate,
}: {
  entry: NavEntry;
  active: boolean;
  onNavigate: () => void;
}) {
  return (
    <Link
      // next/link, not a plain anchor: a LIFF session lives in the document
      // LINE opened, and a full page load starts a fresh one with no LIFF
      // context at all — the reason the tile grid used Link too.
      href={entry.href}
      className="rail-link"
      aria-current={active ? "page" : undefined}
      // The label is the accessible name whether or not it is on screen:
      // collapsed, the text is hidden and this is all a screen reader has.
      aria-label={entry.label}
      title={entry.label}
      onClick={onNavigate}
    >
      <span className="rail-icon">{entry.icon}</span>
      <span className="rail-label">{entry.label}</span>
    </Link>
  );
}

/**
 * The menu button for the top bar. Present only on the phone shape of the
 * rail — on a wide screen the rail is already on screen and a button that
 * opens what is open is noise.
 */
export function NavMenuButton() {
  const { t } = useLanguage();
  const context = useContext(NavContext);
  if (!context?.enabled) return null;
  return (
    <button
      type="button"
      className="navtoggle"
      ref={context.buttonRef}
      aria-expanded={context.open}
      aria-controls={context.railId}
      onClick={context.openDrawer}
      title={t.dashboard.nav.open}
    >
      <span className="rail-btn-icon">{ICONS.menu}</span>
      <span className="sr-only">{t.dashboard.nav.open}</span>
    </button>
  );
}
