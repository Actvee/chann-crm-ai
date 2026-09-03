"""Application Tier configuration.

Mirrors docs/RUNTIME_CONFIG_CONTRACT.md exactly. Anything the contract marks
REQUIRED_NOT_CONFIGURED defaults to empty here rather than to a placeholder
value, so a missing secret fails loudly instead of silently "working".
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "dev"
    platform_version: str = "0.0.0"
    git_commit: str = "unknown"

    data_base_url: str = "http://localhost:8081"
    admin_secret: str = ""                      # shared with Data Tier

    # A separate, single-purpose secret for Cloud Scheduler to call the
    # reminder sweep. Deliberately NOT the platform-admin JWT/session flow:
    # that flow is built for a person logging in through a browser and
    # issues short-lived, session-tracked tokens (see require_admin's
    # get_platform_admin_session check) — reusing it for a service that
    # calls in once a day forever would mean either a token that never
    # expires (defeating the point of the session table) or a cron job that
    # re-authenticates itself, neither of which this endpoint's caller
    # needs. A static shared secret, checked with a constant-time compare
    # exactly like ADMIN_SECRET already is between tiers, is deliberately
    # the same class of credential this project already trusts for a
    # machine-to-machine call.
    reminder_sweep_secret: str = ""

    # LINE — three platform-level OAs (ADR-004)
    line_customer_channel_secret: str = ""
    line_customer_channel_access_token: str = ""
    line_sales_channel_secret: str = ""
    line_sales_channel_access_token: str = ""
    line_technician_channel_secret: str = ""
    line_technician_channel_access_token: str = ""

    # LINE Login channel used as the expected ID-token audience. Full LIFF app
    # IDs belong only in Presentation for liff.init().
    line_login_channel_id: str = ""

    # Platform Admin auth
    jwt_secret: str = ""
    jwt_ttl_s: int = 86400

    # AI — wired in Phase 4. Two model tiers (Master Spec 4.3): the chat tier
    # runs thinking OFF, the reasoning tier is for ad-hoc reports (Phase 17)
    # and keeps thinking ON. Fallback per ADR-014 is done by swapping the slug
    # in these vars, so they stay plain config with no code change needed.
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    openrouter_model_reasoning: str = ""

    # PDF — Phase 10. Uses the official zcatalyst-sdk Python package
    # (confirmed by Zoho's own "Integrate SDK in Third-Party Apps" docs to
    # support exactly this — an external app outside Catalyst, authenticated
    # via a Self Client's OAuth credentials — no need to guess at a raw
    # REST endpoint, which Catalyst does not publicly document for
    # SmartBrowz specifically).
    pdf_renderer: str = "null"                  # null | smartbrowz | local_chromium
    catalyst_api_domain: str = "https://api.catalyst.zoho.com"
    catalyst_project_id: str = ""
    catalyst_environment: str = "Development"
    # ZAID (Zoho Account ID) — a separate, mandatory field the SDK's
    # ICatalystOptions requires alongside project_id (project_key in the
    # SDK's own naming). Confirmed mandatory by inspecting
    # zcatalyst_sdk.types.ICatalystOptions.__required_keys__ directly, not
    # assumed from docs alone. It's a per-environment project identifier
    # (Development and Production each have their own), found in the
    # Catalyst console under Project Settings -> Environments -> General —
    # NOT tied to setting up Catalyst's own Authentication component at
    # all (an earlier assumption based on one specific third-party-
    # integration doc's example, which happened to use Authentication for
    # an unrelated reason; the "Environment Settings" help page confirms
    # ZAID/API Key/Application URL are simply displayed there directly).
    catalyst_zaid: str = ""

    # SmartBrowz OAuth (Master Spec 10.6) — a separate accounts-domain
    # token exchange from the api-domain calls above. accounts_url is
    # datacenter-specific (accounts.zoho.com / accounts.zoho.eu / etc. —
    # whichever matches the Catalyst project's own datacenter); the wrong
    # one will reject the refresh_token outright. client_id/client_secret
    # come from the Catalyst API Console's Self Client; refresh_token is
    # the one-time grant token already exchanged (never regenerated
    # automatically — only the access_token this refresh_token produces
    # expires and gets renewed).
    smartbrowz_accounts_url: str = "https://accounts.zoho.com"
    smartbrowz_client_id: str = ""
    smartbrowz_client_secret: str = ""
    smartbrowz_refresh_token: str = ""

    # Object storage for generated documents (Master Spec 10.3).
    # Empty means storage is not configured: the document store factory
    # returns NullDocumentStore, which refuses loudly rather than letting a
    # generated_documents row be written with nowhere to point.
    # Authentication is Application Default Credentials — on Cloud Run that
    # is the attached service account, so there is deliberately no key or
    # secret here to configure or leak.
    gcs_bucket_name: str = ""
    gcp_project_id: str = ""

    # Deep links from chat into the dashboard (Phase 10).
    #
    # The LIFF id, not the Cloud Run hostname: https://liff.line.me/<id>/<path>
    # opens inside the LINE app with the session already established, so a
    # tap from a chat reply lands on an authenticated page. A raw Cloud Run
    # URL would open an external browser with no LIFF context, and every
    # dashboard page would immediately fail its ID-token check.
    #
    # Empty is a supported state: chat then simply omits the link rather
    # than emitting a broken one.
    #
    # One per OA: a technician's ticket list must deep-link into the
    # technician LIFF app, not the sales one, whose pages refuse their
    # token (3 Sep chat audit).
    liff_sales_id: str = ""
    liff_technician_id: str = ""
    liff_customer_id: str = ""

    # This service's own externally reachable base URL, used to build links
    # sent into LINE chats (issued documents, and anything similar later).
    # Empty means such links are omitted rather than emitted broken.
    public_base_url: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

OA_CHANNELS = ("customer", "sales", "technician")


def channel_secret(oa: str) -> str:
    return getattr(settings, f"line_{oa}_channel_secret", "")


def channel_access_token(oa: str) -> str:
    return getattr(settings, f"line_{oa}_channel_access_token", "")
