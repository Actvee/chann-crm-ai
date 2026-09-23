import ApiKeys from "./ApiKeys";

export const dynamic = "force-dynamic";

export default function SalesApiKeysPage() {
  return <ApiKeys liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
