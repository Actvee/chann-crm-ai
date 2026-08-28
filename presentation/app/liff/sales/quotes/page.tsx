import QuoteList from "./QuoteList";

export const dynamic = "force-dynamic";

export default function SalesQuotesPage() {
  return <QuoteList liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
