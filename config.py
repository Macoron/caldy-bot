from pydantic import BaseModel


class AgentConfig(BaseModel):
    model: str
    system_prompt: str
    timezone: str
    history_window: int = 5  # number of user exchanges to keep (each exchange may include tool calls)
    compress_history: bool = False  # if True, strips intermediate tool calls before saving


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
