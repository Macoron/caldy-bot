import asyncio
import logging
from datetime import datetime, timedelta

from assistant import Assistant

logger = logging.getLogger(__name__)


async def agenda_loop(bot, chat_id, agent_config, tz, agenda_config):
    """Sleep until send_time each day, wake the agent to generate today's agenda."""
    logger.info("Agenda loop started | send_time=%s", agenda_config.send_time)

    while True:
        now = datetime.now(tz)
        hour, minute = map(int, agenda_config.send_time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        sleep_seconds = (target - now).total_seconds()
        logger.info("Agenda loop: next send at %s (in %.0f seconds)", target, sleep_seconds)
        await asyncio.sleep(sleep_seconds)

        try:
            async def notify(text):
                await bot.send_message(chat_id=chat_id, text=text)

            response = await Assistant(agent_config, notify=notify).chat(
                "What's my agenda for today?"
            )
            await bot.send_message(chat_id, response)
            logger.info("Daily agenda sent")
        except Exception:
            logger.exception("Error sending daily agenda")

        # Prevent double-fire if we wake up slightly early
        await asyncio.sleep(60)
