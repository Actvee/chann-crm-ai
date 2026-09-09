"use client";

import { useEffect, useState } from "react";

import { ADMIN } from "@/lib/admin-copy";
import { applyNavCollapsed, readNavCollapsed, writeNavCollapsed } from "@/lib/nav-state";

/**
 * Collapses the console's rail to icons, and remembers it — the same
 * preference and the same storage key as the LIFF dashboards, so a person
 * who works in both gets the shape they chose in both.
 *
 * The layout around it stays a server component: this button only flips
 * the attribute the root layout's inline script already set, and CSS does
 * the rest. Nothing here restructures the pages.
 */
export function AdminRailToggle() {
  // Default on the server and on the first client render, then adopt the
  // stored value. Reading storage during render would make the two markups
  // disagree; the pre-paint script is what stops the rail flashing wide.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(readNavCollapsed());
  }, []);

  const label = collapsed ? ADMIN.nav.expand : ADMIN.nav.collapse;

  return (
    <button
      type="button"
      className="pa-rail-toggle"
      aria-expanded={!collapsed}
      aria-controls="pa-nav"
      title={label}
      onClick={() =>
        setCollapsed((current) => {
          const next = !current;
          writeNavCollapsed(next);
          applyNavCollapsed(next);
          return next;
        })
      }
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
      >
        {collapsed ? <path d="M9.5 6.5 15 12l-5.5 5.5" /> : <path d="M14.5 6.5 9 12l5.5 5.5" />}
      </svg>
      <span className="pa-sr-only">{label}</span>
    </button>
  );
}
