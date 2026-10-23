"""Settings the server reads from the environment at startup."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCALE_", frozen=True)

    # No default: a writable fallback would put the weigh-in history somewhere
    # the operator did not choose, and a container would take it to the grave.
    db_path: Path

    # Every delivery is signed with this and refused without it, so reaching the
    # port is not enough to write to the weigh-in history. No default: a key the
    # server picked for itself would admit anyone who knew what it picks. The
    # floor is a length worth calling a secret — `openssl rand -hex 32`.
    gateway_key: str = Field(min_length=32)

    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # A weigh-in body is under 150 bytes; the ceiling only has to stay clear of that.
    max_body_bytes: int = Field(default=4096, ge=256)

    # A gateway posts once per weigh-in and pings every few minutes, so anything
    # near this ceiling is a client that has stopped behaving.
    rate_limit_per_minute: int = Field(default=120, ge=1)

    # How long a gateway may go quiet before it is called offline, counted in the
    # ping intervals it declares rather than in minutes: the gateway is the one
    # that knows how often it means to speak. Five of them rides out a router
    # reboot without calling a board dead over it.
    device_offline_after_pings: int = Field(default=5, ge=1)

    # How far back a board's ping history reaches. The history is kept to answer
    # what a board was doing around the time it stopped, and a week spans the
    # weekend that nobody was there for; older than that it is ballast. Each
    # board drops its own rows past this as it pings, so nothing has to be swept.
    device_ping_history_days: int = Field(default=7, ge=1)

    # How many readings a page holds when the caller does not say, and the most
    # it may ask for.
    default_page_size: int = Field(default=50, ge=1)
    max_page_size: int = Field(default=500, ge=1)

    @model_validator(mode="after")
    def check_page_sizes(self) -> "Settings":
        if self.default_page_size > self.max_page_size:
            raise ValueError("default_page_size cannot exceed max_page_size")
        return self


def load_settings() -> Settings:
    """Reads every field from the environment, which a type checker cannot see —
    hence the ignore on a call that looks like it is missing its arguments."""
    return Settings()  # pyright: ignore[reportCallIssue]
