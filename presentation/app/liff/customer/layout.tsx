import type { ReactNode } from "react";

/** Customer OA = orange, for every page under this segment (owner rule 5). */
export default function CustomerLayout({ children }: { children: ReactNode }) {
  return <div data-theme="customer">{children}</div>;
}
