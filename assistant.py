import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    UserPromptPart,
    SystemPromptPart,
    ToolReturnPart,
)

from config import AgentConfig
import google_calendar
import todoist

logger = logging.getLogger(__name__)

HISTORY_FILE = Path("history.json")


def _summarize_tool_content(content) -> str:
    """Produce a compact summary of tool return content."""
    if isinstance(content, list):
        return f"[{len(content)} results returned]"
    if isinstance(content, dict):
        return "[1 result returned]"
    if isinstance(content, str) and len(content) > 200:
        return content[:200] + "..."
    return str(content) if content is not None else "[no content]"


def _compress_history(messages: list) -> list:
    """Truncate tool return content while preserving tool call/return structure."""
    compressed = []
    for msg in messages:
        if isinstance(msg, ModelRequest) and any(
            isinstance(p, ToolReturnPart) for p in msg.parts
        ):
            new_parts = []
            for p in msg.parts:
                if isinstance(p, ToolReturnPart):
                    p = ToolReturnPart(
                        tool_name=p.tool_name,
                        content=_summarize_tool_content(p.content),
                        tool_call_id=p.tool_call_id,
                        timestamp=p.timestamp,
                    )
                new_parts.append(p)
            compressed.append(ModelRequest(parts=new_parts))
        else:
            compressed.append(msg)
    return compressed


class Assistant:
    def __init__(self, config: AgentConfig, notify=None):
        self._config = config
        self._notify = notify
        self._system_prompt = self._load_prompt(config.system_prompt)
        self._history: list = self._load_history()

        logger.info("Initializing agent | model=%s", config.model)
        self._agent = Agent(config.model)
        self._register_tools()

    @staticmethod
    def _load_prompt(source: str) -> str:
        if source.endswith(".md"):
            return Path(source).read_text()
        return source

    def _register_tools(self):
        google_calendar.register_tools(self._agent, self._config.timezone, self._notify)
        todoist.register_tools(self._agent, self._config.timezone, self._notify)

    def _load_history(self) -> list:
        system_msg = ModelRequest(parts=[SystemPromptPart(content=self._system_prompt)])
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text())
            history = ModelMessagesTypeAdapter.validate_python(data)
            # Strip all system-prompt parts to avoid duplication across restarts
            for m in history:
                if isinstance(m, ModelRequest):
                    m.parts = [p for p in m.parts if not isinstance(p, SystemPromptPart)]
            history = [m for m in history if not (isinstance(m, ModelRequest) and not m.parts)]
            logger.info("Loaded %d messages from history", len(history))
            return [system_msg] + history
        return [system_msg]

    def _save_history(self):
        messages = _compress_history(self._history) if self._config.compress_history else self._history
        # Trim at user turn boundaries to avoid splitting tool_use/tool_result pairs,
        # which would cause an API error. history_window = N means "keep last N user exchanges".
        user_turns = [
            i for i, m in enumerate(messages)
            if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
        ]
        if len(user_turns) > self._config.history_window:
            messages = messages[user_turns[-self._config.history_window]:]
        data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
        HISTORY_FILE.write_text(json.dumps(data, indent=2))

    async def chat(self, text: str) -> str:
        tz = ZoneInfo(self._config.timezone)
        now = datetime.now(tz)
        text = f"[{now.strftime('%A, %Y-%m-%d %H:%M')} {self._config.timezone}] {text}"
        result = await self._agent.run(text, message_history=self._history)
        self._history = result.all_messages()
        self._save_history()
        usage = result.usage()
        logger.info(
            "Tokens: input=%d output=%d total=%d",
            usage.input_tokens, usage.output_tokens, usage.total_tokens
        )
        return result.output
