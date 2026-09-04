"use client";

import { usePathname } from "next/navigation";

import { ADMIN } from "@/lib/admin-copy";

const ITEMS = [
  {
    href: "/admin",
    label: ADMIN.nav.tenants,
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d="M3 9.5 12 4l9 5.5M5 10v9h14v-9M10 19v-5h4v5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    href: "/admin/audit",
    label: ADMIN.nav.audit,
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d="M6 4h9l4 4v12H6zM14 4v5h5M9 13h6M9 17h6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    href: "/admin/pdpa",
    label: ADMIN.nav.pdpa,
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6zM9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
];

export function AdminNav() {
  const pathname = usePathname() ?? "";
  return (
    <nav className="pa-nav" aria-label={ADMIN.nav.label}>
      {ITEMS.map((item) => {
        const current = item.href === "/admin" ? pathname === "/admin" || pathname.startsWith("/admin/tenants") : pathname.startsWith(item.href);
        return (
          <a key={item.href} href={item.href} aria-current={current ? "page" : undefined}>
            {item.icon}
            <span>{item.label}</span>
          </a>
        );
      })}
    </nav>
  );
}
