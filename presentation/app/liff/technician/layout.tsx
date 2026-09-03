import type { ReactNode } from "react";

/** Technician OA = blue, for every page under this segment (owner rule 5). */
export default function TechnicianLayout({ children }: { children: ReactNode }) {
  return <div data-theme="technician">{children}</div>;
}
