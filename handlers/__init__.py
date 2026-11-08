from aiogram import Dispatcher

from handlers import start, secretary, menu


def register_all_handlers(dp: Dispatcher) -> None:
    dp.include_router(menu.router)
    dp.include_router(start.router)
    dp.include_router(secretary.router)
