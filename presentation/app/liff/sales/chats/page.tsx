import SalesChats from "./SalesChats";

export const dynamic = "force-dynamic";

export default function SalesChatsPage() {
  return <SalesChats liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
