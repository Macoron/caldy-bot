from pydantic import BaseModel


class AgentConfig(BaseModel):
    model: str
    system_prompt: str
    timezone: str
    history_window: int = 5
    compress_history: bool = False


class WhisperConfig(BaseModel):
    model: str


class LoggingConfig(BaseModel):
    level: str
    file: str
    format: str


class AppConfig(BaseModel):
    agent: AgentConfig
    whisper: WhisperConfig
    logging: LoggingConfig
