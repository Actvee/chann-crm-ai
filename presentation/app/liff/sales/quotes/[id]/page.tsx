import QuoteDetail from "./QuoteDetail";

export const dynamic = "force-dynamic";

export default async function QuoteDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <QuoteDetail
      liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""}
      quoteId={id}
    />
  );
}
