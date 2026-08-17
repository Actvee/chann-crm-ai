import { notFound } from "next/navigation";

import LiffShell from "./LiffShell";

const AUDIENCES = ["customer", "sales", "technician"] as const;
export const dynamic = "force-dynamic";

export default async function LiffPage({
  params,
}: {
  params: Promise<{ audience: string }>;
}) {
  const { audience } = await params;
  if (!AUDIENCES.includes(audience as (typeof AUDIENCES)[number])) notFound();
  const envName = `NEXT_PUBLIC_LIFF_${audience.toUpperCase()}_ID`;
  return (
    <LiffShell
      audience={audience as (typeof AUDIENCES)[number]}
      liffId={process.env[envName] ?? ""}
    />
  );
}
