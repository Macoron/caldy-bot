import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from typing import Callable, Awaitable, Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import Message, BotCommand, TelegramObject
from aiogram.filters import Command

from config import config
from assistant import HISTORY_FILE, Assistant
from google_calendar import reminder_loop


# --- Load config ---

load_dotenv()

logging.basicConfig(
    level=getattr(logging, config.logging.level),
    format=config.logging.format,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.logging.file),
    ],
)
logger = logging.getLogger(__name__)

# --- Bot setup ---

bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
dp = Dispatcher()
openai_client = AsyncOpenAI()


@asynccontextmanager
async def typing_indicator(chat_id: int):
    async def keep_typing():
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)

    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        task.cancel()


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message: Message = data.get("event_update").message
        if message and message.chat.id != int(os.environ["TELEGRAM_ALLOWED_CHAT_ID"]):
            logger.warning("Unauthorized access attempt from chat_id=%s", message.chat.id)
            return
        return await handler(event, data)


dp.message.middleware(AuthMiddleware())


async def transcribe_voice(message: Message) -> str:
    file = await bot.get_file(message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
        await bot.download_file(file.file_path, tmp.name)
        with open(tmp.name, "rb") as f:
            result = await openai_client.audio.transcriptions.create(
                model=config.whisper.model,
                file=f,
            )
    return result.text


@dp.message(Command("clear"))
async def handle_clear(message: Message):
    HISTORY_FILE.unlink(missing_ok=True)
    await message.answer("History cleared.")


@dp.message()
async def handle_message(message: Message):
    if message.voice:
        async with typing_indicator(message.chat.id):
            text = await transcribe_voice(message)
        logger.info("user (voice → text): %s", text)
    elif message.text:
        text = message.text
        logger.info("user: %s", text)
    else:
        return

    async def notify(text: str):
        await bot.send_message(chat_id=message.chat.id, text=text)

    async with typing_indicator(message.chat.id):
        response = await Assistant(config.agent, notify=notify).chat(text)

    logger.info("assistant: %s", response)
    await message.answer(response)


async def main():
    await bot.set_my_commands([
        BotCommand(command="clear", description="Clear conversation history"),
    ])
    asyncio.create_task(
        reminder_loop(bot, int(os.environ["TELEGRAM_ALLOWED_CHAT_ID"]),
                      os.environ["GOOGLE_CALENDAR_ID"], config.tz, config.reminders)
    )
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
