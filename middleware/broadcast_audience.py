"""Record only users who actually initiate a private conversation with this bot."""
from aiogram import BaseMiddleware
from aiogram.types import Message
from services.broadcast import subscribe
import logging


class BroadcastAudienceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if (isinstance(event, Message) and event.chat.type == "private" and event.from_user
                and not event.from_user.is_bot and not event.business_connection_id
                and event.chat.id == event.from_user.id
                and (event.text or "").split("@", 1)[0].split(maxsplit=1)[:1] != ["/forget"]):
            try:
                await subscribe(event.from_user.id)
            except Exception as exc:
                logging.getLogger("secretary").warning("broadcast audience failed type=%s", type(exc).__name__)
        return await handler(event, data)
