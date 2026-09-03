import type { ReactNode } from "react";

/**
 * Every sales page is green (owner rule 5: Sale #178a50, Tech #1f6fd6,
 * CS #e8731a). A route-segment layout is the one place that reaches all
 * thirteen sales pages, including the two that do not render AppShell
 * (the menu and the roles page) and the reports view that is shared with
 * the technician OA — each OA's segment paints it in its own colour.
 */
export default function SalesLayout({ children }: { children: ReactNode }) {
  return <div data-theme="sales">{children}</div>;
}
