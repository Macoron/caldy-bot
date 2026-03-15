import tomllib
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class AgentConfig(BaseModel):
    model: str
    system_prompt: str
    timezone: str
    history_window: int  # number of user exchanges to keep (each exchange may include tool calls)
    compress_history: bool  # if True, strips intermediate tool calls before saving


class WhisperConfig(BaseModel):
    model: str


class LoggingConfig(BaseModel):
    level: str
    file: str
    format: str


class RemindersConfig(BaseModel):
    poll_interval_minutes: int
    reminder_minutes: int 
    notified_file: str


class AppConfig(BaseModel):
    agent: AgentConfig
    whisper: WhisperConfig
    reminders: RemindersConfig
    logging: LoggingConfig

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.agent.timezone)


config: AppConfig
with open("config.toml", "rb") as f:
    config = AppConfig.model_validate(tomllib.load(f))

