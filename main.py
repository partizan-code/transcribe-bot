import asyncio
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import config
from aiogram import Bot, Dispatcher

dp = Dispatcher()

async def main() -> None:
    bot = Bot(token=config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())