"""Commands end pending inline edits so later text is never saved unexpectedly."""
from aiogram import BaseMiddleware
from aiogram.types import Message


class CommandClearsMenuState(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            state = data.get("state")
            if state is not None:
                await state.clear()
        return await handler(event, data)
