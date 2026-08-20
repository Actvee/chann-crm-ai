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

    # PDF — Phase 10. SmartBrowz is called over REST, not the Catalyst SDK,
    # because this tier runs on Cloud Run outside Catalyst.
    pdf_renderer: str = "null"                  # null | smartbrowz | local_chromium
    catalyst_api_domain: str = "https://api.catalyst.zoho.com"
    catalyst_project_id: str = ""
    catalyst_environment: str = "Development"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

OA_CHANNELS = ("customer", "sales", "technician")


def channel_secret(oa: str) -> str:
    return getattr(settings, f"line_{oa}_channel_secret", "")


def channel_access_token(oa: str) -> str:
    return getattr(settings, f"line_{oa}_channel_access_token", "")
