import CustomerHome from "./CustomerHome";

export const dynamic = "force-dynamic";

export default function CustomerHomePage() {
  return <CustomerHome liffId={process.env.NEXT_PUBLIC_LIFF_CUSTOMER_ID ?? ""} />;
}
