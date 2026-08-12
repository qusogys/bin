import os
from aiogram import Dispatcher
from aiogram import Bot
from aiogram import F
from aiogram.types import Message
from app.bot import dp, bot
from app.config import settings

# This module launches the bot. It supports two modes controlled by the env var USE_WEBHOOK:
# - polling (default)
# - webhook (if USE_WEBHOOK=1). When using webhook, set WEBHOOK_URL and PORT.

USE_WEBHOOK = os.getenv('USE_WEBHOOK','0') in ('1','true','yes')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_PATH = os.getenv('WEBHOOK_PATH','/webhook')
PORT = int(os.getenv('PORT','8080'))
HOST = os.getenv('HOST','0.0.0.0')


def run_polling():
    from aiogram import executor
    print('Starting polling...')
    executor.start_polling(dp, bot=bot)


def run_webhook():
    from aiogram import executor

    async def on_startup(dispatcher: Dispatcher):
        # set webhook
        url = WEBHOOK_URL.rstrip('/') + WEBHOOK_PATH
        await bot.set_webhook(url)
        print('Webhook set to', url)

    async def on_shutdown(dispatcher: Dispatcher):
        await bot.delete_webhook()

    print('Starting webhook...')
    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host=HOST,
        port=PORT,
    )


def main():
    if USE_WEBHOOK:
        if not WEBHOOK_URL:
            print('USE_WEBHOOK is set but WEBHOOK_URL is not defined. Falling back to polling.')
            run_polling()
        else:
            run_webhook()
    else:
        run_polling()

if __name__ == '__main__':
    main()
