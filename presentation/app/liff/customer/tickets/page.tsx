import { redirect } from "next/navigation";

/**
 * The pre-Phase-14 customer page. LIFF endpoints registered in the LINE
 * console before the customer home existed still point here, which is how
 * the owner saw a screen with only claim/report on it (3 Sep) while the
 * full home was one path up. Whichever endpoint LINE opens, the person
 * lands on the home — WITH the query string: LINE's `liff.state` (a rich
 * menu deep link such as /reports) and the authorisation `code` ride on
 * it, and dropping them left the home blank (review D8, 6 Sep 2026).
 */
export const dynamic = "force-dynamic";

export default async function CustomerTicketsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    for (const item of Array.isArray(value) ? value : value == null ? [] : [value]) {
      query.append(key, item);
    }
  }
  const suffix = query.toString();
  redirect(suffix ? `/liff/customer?${suffix}` : "/liff/customer");
}
