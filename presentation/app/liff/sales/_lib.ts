/**
 * The Sales dashboard's view of the shared LIFF helpers.
 *
 * Re-exported rather than duplicated: technician and customer need the
 * same session bootstrap, and three copies of an auth flow is three places
 * for it to drift — which is how the Sales pages ended up with a hardcoded
 * "sales" audience in the first place.
 */
export {
  LIFF_SDK_SRC,
  SALES_BASE_PATH,
  basePathFor,
  completeLiffRedirect,
  fetchPermissions,
  getLiff,
  initLiffSession,
  liffDiagnostics,
  openExternal,
  proxyHeaders,
} from "../_shared";
export type { Audience, Membership } from "../_shared";
