from aiogram import Bot, Dispatcher
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
import asyncio, os, threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, json, hmac, hashlib, uuid
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = "https://kassabox-miniapp.vercel.app"

bot = Bot(token=str(BOT_TOKEN) if BOT_TOKEN else "")
dp = Dispatcher()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["https://kassabox-miniapp.vercel.app"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DB_PATH = "kassabox.db"

def validate_init_data(init_data: str) -> dict:
    if not init_data: raise Exception("No initData")
    try:
        params = {}
        for item in init_data.split('&'):
            key, value = item.split('=', 1)
            params[key] = value
        hash_from_client = params.pop('hash', None)
        if not hash_from_client: raise ValueError("No hash")
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if expected_hash != hash_from_client: raise ValueError("Hash mismatch")
        return json.loads(params.get('user', '{}'))
    except Exception as e: raise Exception(f"Invalid: {str(e)}")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS wallets (id TEXT PRIMARY KEY, name TEXT, creator_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS wallet_users (id INTEGER PRIMARY KEY, wallet_id TEXT, telegram_id INTEGER, first_name TEXT, role TEXT, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(wallet_id) REFERENCES wallets(id), UNIQUE(wallet_id, telegram_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, wallet_id TEXT, type TEXT, balance REAL DEFAULT 0, FOREIGN KEY(wallet_id) REFERENCES wallets(id), UNIQUE(wallet_id, type))''')
    c.execute('''CREATE TABLE IF NOT EXISTS operations (id INTEGER PRIMARY KEY, wallet_id TEXT, telegram_id INTEGER, op_type TEXT, from_account INTEGER, to_account INTEGER, amount REAL, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(wallet_id) REFERENCES wallets(id))''')
    conn.commit()
    conn.close()

init_db()

class CreateWalletReq(BaseModel):
    name: str
    initData: str

class JoinWalletReq(BaseModel):
    wallet_id: str
    initData: str

class ExpenseReq(BaseModel):
    account_type: str
    amount: float
    comment: str

class TransferReq(BaseModel):
    from_type: str
    to_type: str
    amount: float
    comment: str

@app.get("/health")
def health(): return {"status": "ok"}

@app.post("/api/wallet/create")
async def create_wallet(req: CreateWalletReq):
    user_data = validate_init_data(req.initData)
    wallet_id = str(uuid.uuid4())[:8]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO wallets VALUES (?, ?, ?, ?)', (wallet_id, req.name, user_data.get('id'), datetime.now()))
    c.execute('INSERT INTO wallet_users (wallet_id, telegram_id, first_name, role) VALUES (?, ?, ?, ?)', (wallet_id, user_data.get('id'), user_data.get('first_name'), 'creator'))
    for t in ['card', 'safe1', 'safe2']:
        c.execute('INSERT INTO accounts (wallet_id, type, balance) VALUES (?, ?, ?)', (wallet_id, t, 0))
    conn.commit()
    conn.close()
    return {"success": True, "wallet_id": wallet_id}

@app.post("/api/wallet/join")
async def join_wallet(req: JoinWalletReq):
    user_data = validate_init_data(req.initData)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id FROM wallets WHERE id = ?', (req.wallet_id,))
    if not c.fetchone(): raise Exception("Wallet not found")
    c.execute('INSERT OR IGNORE INTO wallet_users (wallet_id, telegram_id, first_name, role) VALUES (?, ?, ?, ?)', (req.wallet_id, user_data.get('id'), user_data.get('first_name'), 'member'))
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/wallet/{wallet_id}/balances")
async def get_balances(wallet_id: str, x_init_data: str = None):
    user_data = validate_init_data(x_init_data)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT telegram_id FROM wallet_users WHERE wallet_id = ? AND telegram_id = ?', (wallet_id, user_data.get('id')))
    if not c.fetchone(): raise Exception("Not member")
    c.execute('SELECT type, balance FROM accounts WHERE wallet_id = ?', (wallet_id,))
    accounts = {row[0]: row[1] for row in c.fetchall()}
    conn.close()
    total = sum(accounts.values())
    return {"card": accounts.get('card', 0), "safe1": accounts.get('safe1', 0), "safe2": accounts.get('safe2', 0), "total": total}

@app.post("/api/wallet/{wallet_id}/expense")
async def add_expense(wallet_id: str, req: ExpenseReq, x_init_data: str = None):
    user_data = validate_init_data(x_init_data)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT telegram_id FROM wallet_users WHERE wallet_id = ? AND telegram_id = ?', (wallet_id, user_data.get('id')))
    if not c.fetchone(): raise Exception("Not member")
    c.execute('SELECT id, balance FROM accounts WHERE wallet_id = ? AND type = ?', (wallet_id, req.account_type))
    result = c.fetchone()
    if not result or result[1] < req.amount: raise Exception("Insufficient funds")
    acc_id = result[0]
    c.execute('UPDATE accounts SET balance = balance - ? WHERE id = ?', (req.amount, acc_id))
    c.execute('INSERT INTO operations (wallet_id, telegram_id, op_type, from_account, amount, comment) VALUES (?, ?, ?, ?, ?, ?)', (wallet_id, user_data.get('id'), 'expense', acc_id, req.amount, req.comment))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/wallet/{wallet_id}/transfer")
async def transfer(wallet_id: str, req: TransferReq, x_init_data: str = None):
    user_data = validate_init_data(x_init_data)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT telegram_id FROM wallet_users WHERE wallet_id = ? AND telegram_id = ?', (wallet_id, user_data.get('id')))
    if not c.fetchone(): raise Exception("Not member")
    c.execute('SELECT id, balance FROM accounts WHERE wallet_id = ? AND type = ?', (wallet_id, req.from_type))
    from_result = c.fetchone()
    if not from_result or from_result[1] < req.amount: raise Exception("Insufficient funds")
    c.execute('SELECT id FROM accounts WHERE wallet_id = ? AND type = ?', (wallet_id, req.to_type))
    to_result = c.fetchone()
    if not to_result: raise Exception("Invalid target")
    from_id, to_id = from_result[0], to_result[0]
    c.execute('UPDATE accounts SET balance = balance - ? WHERE id = ?', (req.amount, from_id))
    c.execute('UPDATE accounts SET balance = balance + ? WHERE id = ?', (req.amount, to_id))
    c.execute('INSERT INTO operations (wallet_id, telegram_id, op_type, from_account, to_account, amount, comment) VALUES (?, ?, ?, ?, ?, ?, ?)', (wallet_id, user_data.get('id'), 'transfer', from_id, to_id, req.amount, req.comment))
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/wallet/{wallet_id}/operations")
async def get_operations(wallet_id: str, limit: int = 100, x_init_data: str = None):
    user_data = validate_init_data(x_init_data)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT telegram_id FROM wallet_users WHERE wallet_id = ? AND telegram_id = ?', (wallet_id, user_data.get('id')))
    if not c.fetchone(): raise Exception("Not member")
    c.execute('SELECT id, op_type, amount, comment, created_at FROM operations WHERE wallet_id = ? ORDER BY created_at DESC LIMIT ?', (wallet_id, limit))
    ops = [{"id": row[0], "type": row[1], "amount": row[2], "comment": row[3], "date": row[4]} for row in c.fetchall()]
    conn.close()
    return {"operations": ops}

@app.get("/api/wallet/{wallet_id}/users")
async def get_wallet_users(wallet_id: str, x_init_data: str = None):
    user_data = validate_init_data(x_init_data)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT telegram_id, role FROM wallet_users WHERE wallet_id = ? AND telegram_id = ?', (wallet_id, user_data.get('id')))
    if not c.fetchone(): raise Exception("Not member")
    c.execute('SELECT telegram_id, first_name, role FROM wallet_users WHERE wallet_id = ?', (wallet_id,))
    users = [{"telegram_id": row[0], "first_name": row[1], "role": row[2]} for row in c.fetchall()]
    conn.close()
    return {"users": users}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть Kassabox", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("Привет! Это Kassabox.\nНажми кнопку ниже, чтобы открыть мини-приложение 🚀", reply_markup=kb)

async def bot_polling():
    await dp.start_polling(bot)

def run_bot():
    asyncio.run(bot_polling())

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.getenv("PORT", 8000))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
