from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    DEVELOPMENT = "development"
    OIDC = "oidc"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OBSION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8080, ge=1, le=65535)
    api_public_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    allowed_origins: list[str] = ["http://localhost:3000"]
    database_url: str = "postgresql+asyncpg://obsion:obsion@localhost:5432/obsion"
    database_echo: bool = False
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_pool_max_overflow: int = Field(default=20, ge=0, le=200)
    redis_url: str = "redis://localhost:6379/0"
    object_store_endpoint: AnyHttpUrl = AnyHttpUrl("http://localhost:9000")
    object_store_access_key: SecretStr = SecretStr("obsion")
    object_store_secret_key: SecretStr = SecretStr("change-this-local-secret")
    object_store_bucket: str = "obsion-artifacts"
    # "auto" keeps the historical behaviour (in-memory under TEST, MinIO
    # otherwise); the explicit values select a backend in any environment so
    # integration drills can exercise the real S3-compatible write path.
    object_store_backend: Literal["auto", "memory", "minio"] = "auto"
    auth_mode: AuthMode = AuthMode.DEVELOPMENT
    dev_organization_id: UUID = UUID("00000000-0000-7000-8000-000000000001")
    dev_user_id: UUID = UUID("00000000-0000-7000-8000-000000000002")
    dev_bearer_token: SecretStr = Field(
        default=SecretStr("local-development-only-change-me"),
        min_length=16,
        max_length=512,
    )
    oidc_issuer: AnyHttpUrl | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: AnyHttpUrl | None = None
    oidc_algorithms: list[str] = ["RS256", "ES256"]
    auth_session_cookie_name: str = Field(
        default="obsion_session", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    auth_session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=300, le=7 * 24 * 60 * 60)
    auth_session_retention_days: int = Field(default=30, ge=1, le=365)
    run_max_steps: int = Field(default=30, ge=1, le=200)
    run_max_critic_replans: int = Field(default=1, ge=0, le=3)
    run_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    run_max_input_tokens: int = Field(default=120_000, ge=1_000, le=10_000_000)
    run_max_output_tokens: int = Field(default=16_000, ge=256, le=1_000_000)
    run_max_cost_amount: Decimal = Field(default=Decimal("10"), gt=0, le=1_000_000)
    run_worker_concurrency: int = Field(default=8, ge=1, le=128)
    automation_enabled: bool = True
    automation_worker_concurrency: int = Field(default=4, ge=1, le=64)
    automation_poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=30)
    automation_lease_seconds: int = Field(default=30, ge=10, le=300)
    actions_enabled: bool = True
    action_worker_concurrency: int = Field(default=2, ge=1, le=32)
    action_poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=30)
    action_lease_seconds: int = Field(default=30, ge=10, le=300)
    event_stream_heartbeat_seconds: int = Field(default=15, ge=5, le=60)
    app_server_initialize_timeout_seconds: int = Field(default=10, ge=2, le=60)
    app_server_max_message_bytes: int = Field(default=256 * 1024, ge=4096, le=4 * 1024 * 1024)
    app_server_max_subscriptions: int = Field(default=32, ge=1, le=256)
    app_server_event_poll_interval_seconds: float = Field(default=0.25, ge=0.05, le=5)
    app_server_idempotency_retention_hours: int = Field(default=24, ge=1, le=24 * 30)
    operator_capability_idempotency_retention_hours: int = Field(
        default=24 * 7,
        ge=24,
        le=24 * 365,
    )
    capability_rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    rate_limit_fail_closed: bool = True
    sql_default_limit: int = Field(default=500, ge=1, le=10_000)
    sql_max_limit: int = Field(default=5_000, ge=1, le=100_000)
    sql_scan_budget: int = Field(default=1_000_000, ge=1, le=1_000_000_000)
    sql_require_explicit_limit: bool = True
    sql_timeout_seconds: int = Field(default=30, ge=1, le=300)
    model_request_timeout_seconds: int = Field(default=120, ge=5, le=600)
    model_allowed_hosts: list[str] = []
    model_force_private_for_sensitive: bool = True
    model_private_profile_name: str = Field(
        default="private", min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )
    document_max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=250 * 1024 * 1024)
    code_graph_max_files: int = Field(default=500, ge=1, le=20_000)
    code_graph_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=250 * 1024 * 1024)
    artifact_max_upload_bytes: int = Field(
        default=100 * 1024 * 1024, ge=1024, le=2 * 1024 * 1024 * 1024
    )
    attachment_context_max_chars: int = Field(default=80_000, ge=1_000, le=1_000_000)
    conversation_context_max_turns: int = Field(default=20, ge=1, le=200)
    conversation_context_max_chars: int = Field(default=48_000, ge=1_000, le=500_000)
    conversation_context_max_chars_per_message: int = Field(default=8_000, ge=256, le=100_000)
    memory_default_ttl_days: int = Field(default=365, ge=1, le=3650)
    memory_max_ttl_days: int = Field(default=3650, ge=1, le=3650)
    memory_max_context_items: int = Field(default=40, ge=1, le=500)
    memory_max_context_chars: int = Field(default=24_000, ge=1_000, le=500_000)
    knowledge_max_candidates: int = Field(default=2000, ge=100, le=20_000)
    knowledge_max_results: int = Field(default=50, ge=1, le=200)
    knowledge_embedding_profile: str | None = None
    knowledge_embedding_batch_size: int = Field(default=64, ge=1, le=256)
    secret_encryption_key: SecretStr | None = None
    otel_enabled: bool = True
    otel_service_name: str = "obsion-control-plane"
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    otel_exporter_headers: SecretStr | None = None
    otel_trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator(
        "oidc_issuer",
        "oidc_audience",
        "oidc_jwks_url",
        "secret_encryption_key",
        "otel_exporter_otlp_endpoint",
        "otel_exporter_headers",
        "knowledge_embedding_profile",
        mode="before",
    )
    @classmethod
    def empty_optional_values_are_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if self.environment == Environment.PRODUCTION and self.auth_mode == AuthMode.DEVELOPMENT:
            raise ValueError("development authentication cannot be used in production")
        if self.environment == Environment.PRODUCTION and "*" in self.allowed_origins:
            raise ValueError("production browser sessions require explicit allowed origins")
        if self.auth_mode == AuthMode.OIDC and (
            not self.oidc_issuer or not self.oidc_audience or not self.oidc_jwks_url
        ):
            raise ValueError("OIDC mode requires issuer, audience, and JWKS URL")
        if self.sql_default_limit > self.sql_max_limit:
            raise ValueError("sql_default_limit cannot exceed sql_max_limit")
        if self.automation_lease_seconds <= self.automation_poll_interval_seconds:
            raise ValueError("automation lease must exceed the polling interval")
        if self.action_lease_seconds <= self.action_poll_interval_seconds:
            raise ValueError("action lease must exceed the polling interval")
        if self.memory_default_ttl_days > self.memory_max_ttl_days:
            raise ValueError("memory_default_ttl_days cannot exceed memory_max_ttl_days")
        if self.conversation_context_max_chars_per_message > self.conversation_context_max_chars:
            raise ValueError(
                "conversation_context_max_chars_per_message cannot exceed "
                "conversation_context_max_chars"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
