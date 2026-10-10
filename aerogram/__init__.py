"""aerogram — automate Instagram direct messages from Python.

Quickstart::

    from aerogram import Client, filters

    app = Client("my_session", cookies_file="session/cookies.txt")

    @app.on_message(filters.text & ~filters.self)
    async def echo(client, message):
        await message.reply_text(message.text)

    app.run()
"""
from .client import Client
from .dispatcher import (MESSAGE, MESSAGE_DELETE, RAW_DELTA, THREAD_UPDATE,
                         UNSEEN_COUNT)
from .errors import (AuthError, ChallengeRequired, InstaDMError, NotFoundError,
                     ProtocolError, RateLimited, RealtimeError, SendError)
from .filters import Chat, FromUser, Regex, create, filters
from .session import Session
from .types import Media, Message, Reaction, Thread, User

__version__ = "0.2.0"

__all__ = [
    "Client", "Session", "Message", "Thread", "User", "Media", "Reaction",
    "filters", "Chat", "FromUser", "Regex", "create",
    "MESSAGE", "MESSAGE_DELETE", "THREAD_UPDATE", "UNSEEN_COUNT", "RAW_DELTA",
    "InstaDMError", "AuthError", "ChallengeRequired", "RateLimited",
    "NotFoundError", "ProtocolError", "RealtimeError", "SendError",
]
