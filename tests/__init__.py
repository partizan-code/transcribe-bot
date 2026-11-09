"""Test-only credentials and a fail-closed external network guard."""

import os
import socket
from unittest.mock import patch

from loguru import logger

logger.remove()
_environment = patch.dict(
    os.environ,
    {
        "BOT_TOKEN": "123456:TEST_ONLY_TOKEN",
        "ASSEMBLYAI_API_KEY": "test-only-key",
        "ALLOWED_USER_IDS": "1,2,3",
        "PYTHON_DOTENV_DISABLED": "1",
    },
)
_environment.start()
_connect = socket.socket.connect


def _local_connect(self: socket.socket, address: object) -> object:
    # Windows asyncio creates a local socket pair for its event loop.
    if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
        return _connect(self, address)
    raise AssertionError("External network is forbidden in tests")


_network = patch.object(socket.socket, "connect", _local_connect)
_network.start()
_network_ex = patch.object(
    socket.socket, "connect_ex", side_effect=AssertionError("External network is forbidden")
)
_network_ex.start()
