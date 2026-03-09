import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
)

from config import AgentConfig

logger = logging.getLogger(__name__)

HISTORY_FILE = Path("history.json")


def _compress_history(messages: list) -> list:
    """Strip intermediate tool calls, keeping only user requests and final text responses."""
    compressed = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        is_user_turn = isinstance(msg, ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in msg.parts
        )
        if is_user_turn:
            final_response = None
            j = i + 1
            while j < len(messages):
                next_msg = messages[j]
                is_next_user = isinstance(next_msg, ModelRequest) and any(
                    isinstance(p, UserPromptPart) for p in next_msg.parts
                )
                if is_next_user:
                    break
                if isinstance(next_msg, ModelResponse) and any(
                    isinstance(p, TextPart) for p in next_msg.parts
                ):
                    final_response = next_msg
                j += 1
            compressed.append(msg)
            if final_response:
                compressed.append(final_response)
            i = j
        else:
            i += 1
    return compressed


class Assistant:
    def __init__(self, config: AgentConfig):
        self._config = config
        self._history: list = self._load_history()

        logger.info("Initializing agent | model=%s", config.model)

        self._agent = Agent(config.model)
        self._register_tools()

        @self._agent.system_prompt
        def dynamic_system_prompt() -> str:
            prompt = self._render_prompt(self._config.system_prompt)
            logger.info("System prompt: %s", prompt)
            return prompt

    def _render_prompt(self, template: str) -> str:
        tz = ZoneInfo(self._config.timezone)
        now = datetime.now(tz)
        return template.format(
            now=now.strftime("%Y-%m-%d %H:%M"),
            weekday=now.strftime("%A"),
        )

    def _register_tools(self):
        @self._agent.tool_plain
        def list_folder(path: str) -> str:
            """List contents of a folder. Returns file and directory names."""
            import os
            target = Path(path).expanduser().resolve()
            logger.info("Tool called: list_folder → %s", target)
            if not target.is_dir():
                return f"Not a directory: {target}"
            entries = sorted(os.listdir(target))
            return "\n".join(entries) if entries else "(empty)"

    def _load_history(self) -> list:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text())
            history = ModelMessagesTypeAdapter.validate_python(data)
            logger.info("Loaded %d messages from history", len(history))
            return history
        return []

    def _save_history(self):
        compressed = _compress_history(self._history)
        trimmed = compressed[-(self._config.history_window * 2):]
        data = ModelMessagesTypeAdapter.dump_python(trimmed, mode="json")
        HISTORY_FILE.write_text(json.dumps(data, indent=2))

    async def chat(self, text: str) -> str:
        result = await self._agent.run(text, message_history=self._history)
        self._history = result.all_messages()
        self._save_history()
        return result.output

    def clear_history(self):
        self._history = []
        HISTORY_FILE.unlink(missing_ok=True)
        logger.info("History cleared")
