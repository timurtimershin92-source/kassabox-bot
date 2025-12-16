from aiogram import Bot, Dispatcher
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
import asyncio
import os
import threading
from fastapi import FastAPI
import uvicorn

BOT_TOKEN = os.getenv("BOT_TOKEN")  # токен из переменной окружения
WEBAPP_URL = "https://kassabox-miniapp.vercel.app"  # твой URL Mini App

bot = Bot(token=str(BOT_TOKEN) if BOT_TOKEN else "")
dp = Dispatcher()
app = FastAPI()

@app.get("/")
def health_check():
    return {"status": "ok", "bot": "running"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Открыть Kassabox",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])
    await message.answer(
        "Привет! Это Kassabox.\n"
        "Нажми кнопку ниже, чтобы открыть мини-приложение 🚀",
        reply_markup=kb
    )

async def bot_polling():
    await dp.start_polling(bot)

def run_bot():
    asyncio.run(bot_polling())

if __name__ == "__main__":
    # Запусти бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Запусти FastAPI в основном потоке
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
