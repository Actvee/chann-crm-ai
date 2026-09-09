"use client";

/**
 * Whether the left navigation is collapsed to icons — one preference,
 * shared by the LIFF dashboards and the platform admin console, because
 * it is one person expressing one taste about one kind of furniture.
 *
 * It is applied to <html data-nav> by an inline script in the root layout
 * before the first paint. Everything else here keeps that attribute and
 * localStorage in step; React only mirrors the value.
 */

export const NAV_STORAGE_KEY = "chann.nav.collapsed";

export function readNavCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(NAV_STORAGE_KEY) === "1";
  } catch {
    // Storage can throw outright (private mode, storage disabled inside an
    // in-app browser). Expanded is the right fallback: nothing is hidden.
    return false;
  }
}

export function writeNavCollapsed(collapsed: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(NAV_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // A remembered preference is never worth failing a click over.
  }
}

export function applyNavCollapsed(collapsed: boolean): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.nav = collapsed ? "collapsed" : "expanded";
}
