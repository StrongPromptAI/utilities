"""Error codes and exception classes for the DevOps agent."""

from enum import Enum


class ErrorCode(str, Enum):
    """Machine-readable error codes used across all modules."""

    OK = "ok"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"
    PROVIDER_DOWN = "provider_down"
    APP_UNHEALTHY = "app_unhealthy"
    CONFIG_ERROR = "config_error"
    NETWORK_ERROR = "network_error"
    GRAPHQL_ERROR = "graphql_error"
    SMTP_ERROR = "smtp_error"


# CLI exit codes — deterministic for cron/automation
EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_PROVIDER_UNAVAILABLE = 4
EXIT_HEALTH_FAILED = 5


class DevOpsError(Exception):
    """Base exception for all DevOps agent errors."""

    def __init__(self, message: str, code: ErrorCode):
        super().__init__(message)
        self.code = code


class ConfigError(DevOpsError):
    """Missing config file, invalid TOML, missing required fields."""

    def __init__(self, message: str):
        super().__init__(message, ErrorCode.CONFIG_ERROR)


class RailwayAPIError(DevOpsError):
    """Railway GraphQL errors or auth failures."""

    def __init__(self, message: str, code: ErrorCode = ErrorCode.GRAPHQL_ERROR):
        super().__init__(message, code)


class HealthCheckError(DevOpsError):
    """Health endpoint unreachable or unexpected status."""

    def __init__(self, message: str):
        super().__init__(message, ErrorCode.APP_UNHEALTHY)


class NotifyError(DevOpsError):
    """SMTP connection or send failures."""

    def __init__(self, message: str):
        super().__init__(message, ErrorCode.SMTP_ERROR)
