import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

import config
from errors import AppError
from handlers import create_router
from logger_config import setup_logging
from media import MediaProcessor
from processing import JobManager
from transcribe import TranscriptionService


async def main() -> None:
    setup_logging()
    try:
        media = MediaProcessor(config)
        media.check_tools()
        try:
            bot = Bot(
                token=config.TOKEN,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
        except Exception:
            raise ValueError("Некорректный BOT_TOKEN") from None
    except (ValueError, AppError) as exc:
        logger.error("Startup configuration error: {}", exc)
        raise SystemExit(1) from None

    async with bot.context():
        transcriber = TranscriptionService(config)
        manager = JobManager(bot, config, transcriber, media)
        try:
            dp = Dispatcher()
            dp.include_router(create_router(config, manager))
            manager.start()
            logger.info(
                "Starting bot polling; workers={} queue={}", config.WORKERS, config.QUEUE_SIZE
            )
            await dp.start_polling(bot, close_bot_session=False)
        finally:
            try:
                await manager.close()
            finally:
                await transcriber.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.error("Bot stopped: {}", type(exc).__name__)
        raise SystemExit(1) from None
