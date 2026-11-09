import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import main
from errors import AppError
from tests.helpers import settings


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_media_tools_stops_before_clients_or_polling(self) -> None:
        with (
            patch("main.setup_logging"),
            patch("main.MediaProcessor") as media,
            patch("main.Bot") as bot,
        ):
            media.return_value.check_tools.side_effect = AppError(
                "media_tools", "Missing media tools"
            )
            with self.assertRaises(SystemExit) as caught:
                await main.main()
            self.assertEqual(caught.exception.code, 1)
            bot.assert_not_called()

    async def test_polling_failure_closes_workers_sdk_and_telegram(self) -> None:
        closed = []

        @asynccontextmanager
        async def context() -> object:
            try:
                yield
            finally:
                closed.append("bot")

        bot = SimpleNamespace(context=context)
        service = SimpleNamespace(aclose=AsyncMock())
        manager = SimpleNamespace(start=Mock(), close=AsyncMock())
        dispatcher = SimpleNamespace(
            include_router=Mock(), start_polling=AsyncMock(side_effect=RuntimeError("test failure"))
        )
        with (
            patch("main.setup_logging"),
            patch("main.config", settings()),
            patch("main.MediaProcessor") as media,
            patch("main.Bot", return_value=bot),
            patch("main.TranscriptionService", return_value=service),
            patch("main.JobManager", return_value=manager),
            patch("main.Dispatcher", return_value=dispatcher),
        ):
            with self.assertRaises(RuntimeError):
                await main.main()
            media.return_value.check_tools.assert_called_once()
        manager.start.assert_called_once()
        manager.close.assert_awaited_once()
        service.aclose.assert_awaited_once()
        self.assertEqual(closed, ["bot"])
