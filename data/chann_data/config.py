"""Data Tier configuration.

Every value here is DERIVED_AT_DEPLOY or REQUIRED_NOT_CONFIGURED per
docs/RUNTIME_CONFIG_CONTRACT.md. Nothing is hardcoded to a historical value.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "dev"                 # dev | stage | production
    platform_version: str = "0.0.0"
    git_commit: str = "unknown"

    database_url: str = "postgresql+psycopg://chann:chann@localhost:5432/chann_crm_ai"
    redis_url: str = "redis://localhost:6379/0"

    # Reduced-security posture (CLAUDE.md 5): internal endpoints are guarded
    # by a shared secret header, not IAM. This is an accepted limitation and
    # must be declared in every Release Manifest.
    admin_secret: str = ""

    cache_ttl_identity_s: int = 3600     # 1h   per Master Spec 1.8
    cache_ttl_member_s: int = 1800       # 30m  per Master Spec 1.8
    cache_ttl_permissions_s: int = 300   # Phase 2; invalidate on role/member change
    cache_ttl_license_setting_s: int = 300
    cache_ttl_admin_session_s: int = 86400  # 24h per Master Spec 1.8

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
