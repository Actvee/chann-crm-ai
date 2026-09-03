import { redirect } from "next/navigation";

/**
 * The pre-Phase-14 technician page. LIFF endpoints registered in the LINE
 * console before the technician home existed still point here, which is how
 * the owner saw a screen with only claim/report on it (3 Sep) while the
 * full home was one path up. Whichever endpoint LINE opens, the person
 * lands on the home.
 */
export const dynamic = "force-dynamic";

export default function TechnicianTicketsPage() {
  redirect("/liff/technician");
}
