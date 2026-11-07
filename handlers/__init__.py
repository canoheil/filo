from aiogram import Dispatcher

from handlers import start, secretary


def register_all_handlers(dp: Dispatcher) -> None:
    dp.include_router(start.router)
    dp.include_router(secretary.router)
