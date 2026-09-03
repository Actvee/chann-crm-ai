import SalesTeams from "./SalesTeams";

export const dynamic = "force-dynamic";

export default function SalesTeamsPage() {
  return <SalesTeams liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
