import asyncio
import logging
import re
import os
import base64
import json
import uuid
import urllib.parse
from datetime import datetime, timedelta
from decimal import Decimal

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, ParseMode, CallbackQuery,
                           Message, LabeledPrice, ContentTypes)
from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import (SessionPasswordNeededError,
                             PhoneCodeExpiredError,
                             PhoneCodeInvalidError)
from telethon.sessions import StringSession
from tinydb import TinyDB, Query
from tinydb.storages import JSONStorage

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SECOND_BOT_TOKEN = os.getenv("SECOND_BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")
USERBOT_PHONE = os.getenv("USERBOT_PHONE")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
API_SECRET = os.getenv("API_SECRET", "super_secret_key_123")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY")

TON_WALLET = "UQBASapxMnOPXG716Lv1U1jlCUpSJQg82t_H00ryIVM6T-Uv"
USDT_CONTRACT = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
USDT_CONTRACT_USERFRIENDLY = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"

CHANNEL_ID = -1002839409663
CHANNEL_LINK = "https://t.me/+Zb9AutLfLZViZGEy"
SUPPORT_USERNAME = "swordSar"
RULES_LINK = "https://telegra.ph/PhysicHub--Pravila-polzovaniya-servisom-05-06"
REVIEWS_LINK = "https://t.me/repswordSar/89"
BOT_USERNAME = "PhysicHubFiz_Bot"
STARS_BOT_USERNAME = "StarsPaPhuchic_Bot"
GIFT_LINK = "https://t.me/swordSar?text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%2C%20%D1%85%D0%BE%D1%87%D1%83%20%D0%BF%D0%BE%D0%BF%D0%BE%D0%BB%D0%BD%D0%B8%D1%82%D1%8C%20%D0%B1%D0%B0%D0%BB%D0%B0%D0%BD%D1%81%20NFT%20%D0%BF%D0%BE%D0%B4%D0%B0%D1%80%D0%BA%D0%BE%D0%BC."

STARS_RATE = Decimal('0.71')
ITEMS_PER_PAGE = 20

# Базы данных
db = TinyDB("bot_data.json", storage=JSONStorage, indent=4, ensure_ascii=False)
users_table = db.table("users")
accounts_table = db.table("accounts")
orders_table = db.table("orders")
pending_payments = db.table("pending_payments")
sold_accounts_table = db.table("sold_accounts")
referrals_table = db.table("referrals")
banned_table = db.table("banned")
promocodes_table = db.table("promocodes")
topup_history_table = db.table("topup_history")
purchase_history_table = db.table("purchase_history")
pending_deposits = db.table("pending_deposits")
deposit_counter = db.table("deposit_counter")
processed_transactions = db.table("processed_transactions")

# ==================== БЛОКИРОВКА ЗАПИСИ В БАЗУ ====================
db_lock = asyncio.Lock()

async def async_safe_insert(table, data):
    async with db_lock:
        return table.insert(data)

async def async_safe_remove(table, query):
    async with db_lock:
        return table.remove(query)

async def async_safe_update(table, fields, query):
    async with db_lock:
        return table.update(fields, query)

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
second_bot = Bot(token=SECOND_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp_second = Dispatcher(second_bot, storage=storage)

# ==================== HTTP СЕРВЕР ====================
app = web.Application()

async def handle_topup(request):
    try:
        data = await request.json()
        if data.get("secret") != API_SECRET:
            return web.json_response({"status": "error", "message": "Неверный ключ"})
        
        user_id = int(data["user_id"])
        amount = float(data["amount"])
        
        user = users_table.get(Query().user_id == user_id)
        if user:
            old_balance = user.get("balance", 0)
            new_balance = old_balance + amount
            
            users_table.update({"balance": new_balance}, Query().user_id == user_id)
            topup_history_table.insert({
                "user_id": user_id,
                "amount": amount,
                "method": "Stars",
                "created_at": datetime.now().isoformat()
            })
            
            try:
                await bot.send_message(user_id, f"✅ Баланс пополнен на {format_price(amount)} через Stars!")
            except:
                pass
            
            return web.json_response({"status": "ok", "new_balance": new_balance})
        return web.json_response({"status": "error", "message": "Юзер не найден"})
    except Exception as e:
        logging.error(f"Topup error: {e}")
        return web.json_response({"status": "error", "message": str(e)})

app.router.add_post("/topup", handle_topup)

# ==================== FSM ====================
class AddAccount(StatesGroup):
    waiting_country = State()
    waiting_type = State()
    waiting_year = State()
    waiting_price = State()
    waiting_phone = State()
    waiting_password = State()
    waiting_code = State()

class Broadcast(StatesGroup):
    waiting_text = State()

class TopUp(StatesGroup):
    waiting_crypto_amount = State()
    waiting_stars_amount = State()
    waiting_gram_amount = State()
    waiting_usdt_ton_amount = State()

class BanUser(StatesGroup):
    waiting_ban_id = State()

class PromoCode(StatesGroup):
    waiting_promo = State()

class TransferMoney(StatesGroup):
    waiting_user_id = State()
    waiting_amount = State()

# ==================== ПРОВЕРКА БАНА ====================
def is_banned(user_id):
    banned = banned_table.get(Query().user_id == user_id)
    return banned is not None

async def check_ban_and_answer(message: Message):
    if is_banned(message.from_user.id):
        await message.answer("🚫 Ваш тикет закрыт.")
        return True
    return False

# ==================== ФУНКЦИИ ====================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_user(user_id):
    return users_table.get(Query().user_id == user_id)

def create_user_if_not(user_id, username=None, referrer_id=None):
    user = get_user(user_id)
    if not user:
        users_table.insert({
            "user_id": user_id,
            "username": username or "Неизвестный",
            "balance": 0.0,
            "purchases": 0,
            "referrer_id": referrer_id,
            "created_at": datetime.now().isoformat()
        })
        if referrer_id and referrer_id != user_id:
            existing = referrals_table.get(
                (Query().user_id == referrer_id) & (Query().invited_user_id == user_id)
            )
            if not existing:
                referrals_table.insert({
                    "user_id": referrer_id,
                    "invited_user_id": user_id,
                    "has_purchased": False,
                    "reward_claimed": False
                })
    else:
        users_table.update({"username": username or user.get("username", "Неизвестный")}, Query().user_id == user_id)

def update_balance(user_id, new_balance):
    users_table.update({"balance": new_balance}, Query().user_id == user_id)

def format_price(price):
    return f"{Decimal(str(price)).quantize(Decimal('0.01'))} ₽"

def get_referral_count(user_id):
    return len(referrals_table.search(Query().user_id == user_id))

def convert_address_to_raw(address):
    try:
        if ":" in address:
            return address
        
        addr_bytes = base64.urlsafe_b64decode(address + "=" * (-len(address) % 4))
        
        if len(addr_bytes) >= 34:
            hash_part = addr_bytes[2:34]
            hex_hash = hash_part.hex()
            return f"0:{hex_hash}"
        return address
    except Exception as e:
        logging.error(f"Address convert error: {e}")
        return address

def extract_comment(in_msg):
    try:
        message_content = in_msg.get("message_content", {})
        if message_content:
            decoded = message_content.get("decoded", {})
            if decoded:
                for key in ("comment", "text", "value", "msg"):
                    val = decoded.get(key)
                    if val and isinstance(val, str):
                        return val.strip()
                
                body = decoded.get("body")
                if body:
                    try:
                        payload_bytes = base64.b64decode(body)
                        if payload_bytes[:4] == b'\x00\x00\x00\x00':
                            text = payload_bytes[4:].decode('utf-8', errors='ignore').strip()
                            if text:
                                return text
                    except:
                        pass
            
            body = message_content.get("body")
            if body:
                try:
                    payload_bytes = base64.b64decode(body)
                    if payload_bytes[:4] == b'\x00\x00\x00\x00':
                        text = payload_bytes[4:].decode('utf-8', errors='ignore').strip()
                        if text:
                            return text
                except:
                    pass
            
            msg = message_content.get("text")
            if msg:
                return msg.strip()
        
        msg = in_msg.get("message", "")
        if msg:
            return msg.strip()
    except:
        pass
    return ""

TON_WALLET_RAW = None

# ==================== ПАГИНАЦИЯ ====================
def get_country_keyboard_page(acc_type, page=0):
    keyboard = InlineKeyboardMarkup(row_width=5)
    accounts = accounts_table.search(Query().acc_type == acc_type)
    unique_countries = list(set(a["country_code"] for a in accounts))
    
    if not unique_countries:
        keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="main_menu"))
        return keyboard
    
    unique_countries.sort()
    
    total_pages = (len(unique_countries) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_countries = unique_countries[start:end]
    
    for country in page_countries:
        account = accounts_table.get(Query().country_code == country)
        if account:
            keyboard.insert(InlineKeyboardButton(
                f"{account['country_flag']} {account['country_name']}",
                callback_data=f"country_{acc_type}_{country}"
            ))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("[ ← Назад ]", callback_data=f"page_{acc_type}_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("[ → Вперёд ]", callback_data=f"page_{acc_type}_{page+1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    
    keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="main_menu"))
    
    return keyboard

def admin_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ Добавить аккаунт", callback_data="admin_add"),
        InlineKeyboardButton("📊 Остатки", callback_data="admin_stock")
    )
    keyboard.add(
        InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton("💳 Пополнить юзеру", callback_data="admin_topup_user")
    )
    keyboard.add(
        InlineKeyboardButton("📋 Рефералы", callback_data="admin_referrals"),
        InlineKeyboardButton("🚫 Бан юзера", callback_data="admin_ban")
    )
    keyboard.add(
        InlineKeyboardButton("🎫 Создать промокод", callback_data="admin_promo"),
        InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")
    )
    keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="main_menu"))
    return keyboard

def main_menu_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        InlineKeyboardButton("[👛] Купить аккаунт", callback_data="buy_regular"),
        InlineKeyboardButton("[👝] Аккаунт с отлегой", callback_data="buy_aged")
    )
    keyboard.row(InlineKeyboardButton("[🎩] Мой профиль", callback_data="profile"))
    keyboard.row(
        InlineKeyboardButton("[🗞] Правила", url=RULES_LINK),
        InlineKeyboardButton("[📓] Отзывы", url=REVIEWS_LINK)
    )
    if is_admin(user_id):
        keyboard.row(InlineKeyboardButton("🛠 Админ-панель", callback_data="admin_panel"))
    return keyboard

async def show_main_menu(user_id):
    caption = (
        "Добро пожаловать ✈️\n\n"
        "<i>Чем мы лучше других сервисов</i>\n"
        "<blockquote>Моментальная выдача аккаунта.\n"
        "Большой ассортимент аккаунтов.\n"
        "Лучшее качество аккаунтов.</blockquote>"
    )
    await bot.send_photo(user_id, "https://iili.io/BQyyE22.jpg", caption=caption, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard(user_id))

def get_years_keyboard(country_code):
    keyboard = InlineKeyboardMarkup(row_width=3)
    accounts = accounts_table.search((Query().country_code == country_code) & (Query().acc_type == "отлега"))
    years = sorted(list(set(a["year"] for a in accounts if a.get("year") is not None)), reverse=True)
    if not years:
        keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data=f"page_отлега_0"))
        return keyboard
    for year in years:
        keyboard.insert(InlineKeyboardButton(str(year), callback_data=f"year_{country_code}_{year}"))
    keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data=f"page_отлега_0"))
    return keyboard

def get_available_account(acc_type, country_code, year=None):
    search_q = (Query().acc_type == acc_type) & (Query().country_code == country_code)
    accounts = accounts_table.search(search_q)
    
    if year is not None:
        for acc in accounts:
            acc_year = acc.get("year")
            if acc_year is not None and str(acc_year) == str(year):
                return acc
        return None
    
    return accounts[0] if accounts else None

# ==================== CRYPTO BOT ====================
async def get_usdt_rate_from_crypto_bot():
    try:
        url = "https://pay.crypt.bot/api/getExchangeRates"
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                result = await resp.json()
                if result.get("ok"):
                    for rate in result["result"]:
                        if rate["source"] == "USDT" and rate["target"] == "RUB" and rate["is_valid"]:
                            return Decimal('1') / Decimal(rate["rate"])
        return Decimal('0.011')
    except:
        return Decimal('0.011')

async def get_gram_rate():
    try:
        url = "https://pay.crypt.bot/api/getExchangeRates"
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                result = await resp.json()
                if result.get("ok"):
                    for rate in result["result"]:
                        if rate["source"] == "TON" and rate["target"] == "RUB" and rate["is_valid"]:
                            return Decimal(rate["rate"])
        return Decimal('120')
    except:
        return Decimal('120')

async def get_usdt_ton_rate():
    try:
        url = "https://pay.crypt.bot/api/getExchangeRates"
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                result = await resp.json()
                if result.get("ok"):
                    for rate in result["result"]:
                        if rate["source"] == "USDT" and rate["target"] == "RUB" and rate["is_valid"]:
                            return Decimal(rate["rate"])
        return Decimal('95')
    except:
        return Decimal('95')

def generate_deposit_code():
    return f"sword-{uuid.uuid4().hex[:10]}"

def create_tonkeeper_link(amount, comment, jetton=None):
    base = "https://app.tonkeeper.com/transfer/"
    address = TON_WALLET
    if jetton is None:
        nano_amount = int(Decimal(str(amount)) * Decimal('1000000000'))
        params = f"?amount={nano_amount}&text={urllib.parse.quote(comment)}"
    else:
        usdt_amount = int(Decimal(str(amount)) * Decimal('1000000'))
        params = f"?jetton={jetton}&amount={usdt_amount}&text={urllib.parse.quote(comment)}"
    return base + address + params

async def check_ton_transactions():
    global TON_WALLET_RAW
    iteration = 0
    while True:
        try:
            iteration += 1
            await asyncio.sleep(15)
            
            if not TON_WALLET_RAW:
                TON_WALLET_RAW = convert_address_to_raw(TON_WALLET)
            
            headers = {"X-API-Key": TONCENTER_API_KEY}
            logging.info(f"=== ИТЕРАЦИЯ {iteration}: проверка транзакций ===")
            
            # ===== GRAM =====
            try:
                url_gram = f"https://toncenter.com/api/v3/transactions?account={TON_WALLET_RAW}&limit=100"
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url_gram, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        result = await resp.json()
                        transactions = result.get("transactions", [])
                        logging.info(f"GRAM: получил {len(transactions)} транзакций")
                        
                        for tx in transactions:
                            tx_hash = tx.get("hash", "")
                            if not tx_hash:
                                continue
                            
                            already = processed_transactions.get(Query().hash == tx_hash)
                            if already:
                                continue
                            
                            if not tx.get("success") or tx.get("bounced"):
                                continue
                            
                            in_msg = tx.get("in_msg", {})
                            if not in_msg:
                                continue
                            
                            comment = extract_comment(in_msg)
                            raw_value = in_msg.get("value")
                            
                            if not comment:
                                continue
                            
                            logging.info(f"GRAM: comment='{comment}', value={raw_value}")
                            
                            deposit = pending_deposits.get(Query().code == comment)
                            logging.info(f"GRAM: заявка найдена={deposit is not None}")
                            
                            if not deposit:
                                await async_safe_insert(processed_transactions, {
                                    "hash": tx_hash,
                                    "code": comment,
                                    "processed_at": datetime.now().isoformat()
                                })
                                continue
                            
                            if deposit["currency"] == "GRAM":
                                try:
                                    value = int(raw_value)
                                except:
                                    continue
                                
                                gram_amount = Decimal(value) / Decimal('1000000000')
                                needed_gram = Decimal(str(deposit["amount_crypto"]))
                                
                                if gram_amount < Decimal('0.2'):
                                    continue
                                
                                if gram_amount < needed_gram:
                                    await async_safe_remove(pending_deposits, Query().code == comment)
                                    try:
                                        await bot.send_message(
                                            deposit["user_id"],
                                            f"❌ <b>Недоплата!</b>\n\n"
                                            f"Вы отправили: <code>{gram_amount:.4f} GRAM</code>\n"
                                            f"Нужно было: <code>{needed_gram:.4f} GRAM</code>\n\n"
                                            f"Зачисление не выполнено. Обратитесь в поддержку.",
                                            parse_mode=ParseMode.HTML
                                        )
                                    except:
                                        pass
                                    await async_safe_insert(processed_transactions, {
                                        "hash": tx_hash,
                                        "code": comment,
                                        "processed_at": datetime.now().isoformat()
                                    })
                                    continue
                                
                                user = get_user(deposit["user_id"])
                                if user:
                                    new_balance = user["balance"] + float(deposit["amount_rub"])
                                    update_balance(deposit["user_id"], new_balance)
                                    await async_safe_remove(pending_deposits, Query().code == comment)
                                    topup_history_table.insert({
                                        "user_id": deposit["user_id"],
                                        "amount": float(deposit["amount_rub"]),
                                        "method": "GRAM",
                                        "created_at": datetime.now().isoformat()
                                    })
                                    await async_safe_insert(processed_transactions, {
                                        "hash": tx_hash,
                                        "code": comment,
                                        "processed_at": datetime.now().isoformat()
                                    })
                                    try:
                                        await bot.send_message(
                                            deposit["user_id"],
                                            f"✅ Баланс пополнен на {format_price(deposit['amount_rub'])} через GRAM!"
                                        )
                                    except:
                                        pass
                                    logging.info(f"GRAM ЗАЧИСЛЕН АВТОМАТИЧЕСКИ: {comment}")
            except Exception as e:
                logging.error(f"GRAM блок упал: {e}")
            
            # ===== USDT =====
            try:
                url_usdt = f"https://toncenter.com/api/v3/jetton/transfers?owner_address={TON_WALLET_RAW}&direction=in&limit=100"
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url_usdt, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        result = await resp.json()
                        transfers = result.get("jetton_transfers", [])
                        logging.info(f"USDT: получил {len(transfers)} переводов")
                        
                        for transfer in transfers:
                            tx_hash = transfer.get("transaction_hash", "")
                            if not tx_hash:
                                continue
                            
                            already = processed_transactions.get(Query().hash == tx_hash)
                            if already:
                                continue
                            
                            jetton_master = transfer.get("jetton_master", "").lower()
                            if jetton_master != USDT_CONTRACT.lower():
                                continue
                            
                            raw_amount = transfer.get("amount")
                            if raw_amount is None:
                                continue
                            
                            try:
                                amount = int(raw_amount)
                            except:
                                continue
                            
                            usdt_amount = Decimal(amount) / Decimal('1000000')
                            if usdt_amount < Decimal('0.1'):
                                continue
                            
                            forward_payload_b64 = transfer.get("forward_payload", "")
                            if not forward_payload_b64:
                                await async_safe_insert(processed_transactions, {
                                    "hash": tx_hash,
                                    "code": "",
                                    "processed_at": datetime.now().isoformat()
                                })
                                continue
                            
                            try:
                                payload_bytes = base64.b64decode(forward_payload_b64)
                                if payload_bytes[:4] != b'\x00\x00\x00\x00':
                                    await async_safe_insert(processed_transactions, {
                                        "hash": tx_hash,
                                        "code": "",
                                        "processed_at": datetime.now().isoformat()
                                    })
                                    continue
                                comment = payload_bytes[4:].decode('utf-8', errors='ignore').strip()
                            except:
                                continue
                            
                            logging.info(f"USDT: comment='{comment}', amount={usdt_amount}")
                            
                            deposit = pending_deposits.get(Query().code == comment)
                            logging.info(f"USDT: заявка найдена={deposit is not None}")
                            
                            if not deposit:
                                await async_safe_insert(processed_transactions, {
                                    "hash": tx_hash,
                                    "code": comment,
                                    "processed_at": datetime.now().isoformat()
                                })
                                continue
                            
                            if deposit["currency"] == "USDT":
                                needed_usdt = Decimal(str(deposit["amount_crypto"]))
                                
                                if usdt_amount < needed_usdt:
                                    await async_safe_remove(pending_deposits, Query().code == comment)
                                    try:
                                        await bot.send_message(
                                            deposit["user_id"],
                                            f"❌ <b>Недоплата!</b>\n\n"
                                            f"Вы отправили: <code>{usdt_amount:.4f} USDT</code>\n"
                                            f"Нужно было: <code>{needed_usdt:.4f} USDT</code>\n\n"
                                            f"Зачисление не выполнено. Обратитесь в поддержку.",
                                            parse_mode=ParseMode.HTML
                                        )
                                    except:
                                        pass
                                    await async_safe_insert(processed_transactions, {
                                        "hash": tx_hash,
                                        "code": comment,
                                        "processed_at": datetime.now().isoformat()
                                    })
                                    continue
                                
                                user = get_user(deposit["user_id"])
                                if user:
                                    new_balance = user["balance"] + float(deposit["amount_rub"])
                                    update_balance(deposit["user_id"], new_balance)
                                    await async_safe_remove(pending_deposits, Query().code == comment)
                                    topup_history_table.insert({
                                        "user_id": deposit["user_id"],
                                        "amount": float(deposit["amount_rub"]),
                                        "method": "USDT (TON)",
                                        "created_at": datetime.now().isoformat()
                                    })
                                    await async_safe_insert(processed_transactions, {
                                        "hash": tx_hash,
                                        "code": comment,
                                        "processed_at": datetime.now().isoformat()
                                    })
                                    try:
                                        await bot.send_message(
                                            deposit["user_id"],
                                            f"✅ Баланс пополнен на {format_price(deposit['amount_rub'])} через USDT!"
                                        )
                                    except:
                                        pass
                                    logging.info(f"USDT ЗАЧИСЛЕН АВТОМАТИЧЕСКИ: {comment}")
            except Exception as e:
                logging.error(f"USDT блок упал: {e}")
                                
        except Exception as e:
            logging.error(f"TON monitor error: {e}")

async def create_crypto_invoice(amount_rub):
    try:
        rub_to_usdt = await get_usdt_rate_from_crypto_bot()
        amount_usd = Decimal(str(amount_rub)) * rub_to_usdt
        amount_usd = amount_usd.quantize(Decimal('0.01'))
        if amount_usd < Decimal('0.1'):
            amount_usd = Decimal('0.1')
        url = "https://pay.crypt.bot/api/createInvoice"
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
        data = {"asset": "USDT", "amount": str(amount_usd), "description": "Пополнение баланса", "expires_in": 1800}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result["result"]["bot_invoice_url"], result["result"]["invoice_id"]
        return None, None
    except:
        return None, None

async def check_crypto_payment(invoice_id):
    try:
        url = "https://pay.crypt.bot/api/getInvoices"
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
        data = {"invoice_ids": invoice_id}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await resp.json()
                logging.info(f"Ответ API getInvoices: {result}")
                if result.get("ok") and result["result"].get("items"):
                    return result["result"]["items"][0]
                return None
    except Exception as e:
        logging.error(f"Exception: {e}")
        return None

# ==================== ЮЗЕРБОТ ====================
main_userbot = None
userbot_clients = {}

async def start_main_userbot():
    global main_userbot
    try:
        main_userbot = TelegramClient(StringSession(), API_ID, API_HASH)
        await main_userbot.connect()
        if not await main_userbot.is_user_authorized():
            await main_userbot.send_code_request(USERBOT_PHONE)
            for admin_id in ADMIN_IDS:
                await bot.send_message(admin_id, f"🔐 <b>Регистрация юзербота</b>\n\nНомер: {USERBOT_PHONE}\nОтправь код подтверждения.", parse_mode=ParseMode.HTML)
            return False
        else:
            logging.info("Юзербот уже авторизован")
            return True
    except Exception as e:
        logging.error(f"Main userbot error: {e}")
        return False

async def login_userbot_with_code(code):
    global main_userbot
    try:
        await main_userbot.sign_in(USERBOT_PHONE, code)
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, "✅ Юзербот авторизован!")
        return True
    except SessionPasswordNeededError:
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, "⚠️ Нужен облачный пароль! Отправь /pass_main пароль")
        return False
    except Exception as e:
        logging.error(f"Login error: {e}")
        return False

async def create_userbot_for_account(phone, account_id):
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
        userbot_clients[phone] = {"client": client, "account_id": account_id, "awaiting_code": True}
        
        @client.on(events.NewMessage(from_users=777000))
        async def code_handler(event):
            message_text = event.message.message
            code_match = re.search(r'\b(\d{5})\b', message_text)
            if code_match:
                code = code_match.group(1)
                orders = orders_table.search((Query().phone == phone) & (Query().status == "ожидает_код"))
                if orders:
                    order = orders[0]
                    await bot.send_message(order["buyer_id"], f"🔑 Код: <code>{code}</code>", parse_mode=ParseMode.HTML)
                    orders_table.update({"status": "завершен", "code": code}, Query().doc_id == order.doc_id)
                    
                    account = accounts_table.get(doc_id=account_id)
                    if account and account.get("password"):
                        await asyncio.sleep(1)
                        await bot.send_message(
                            order["buyer_id"],
                            f"🔐 Облачный пароль: <code>{account['password']}</code>",
                            parse_mode=ParseMode.HTML
                        )
        return client
    except Exception as e:
        logging.error(f"Create userbot error: {e}")
        return None

async def login_account_userbot(phone, code):
    if phone not in userbot_clients:
        return False
    
    client = userbot_clients[phone]["client"]
    
    try:
        await client.sign_in(phone, code)
        
        if userbot_clients[phone].get("pending_data"):
            account_data = userbot_clients[phone]["pending_data"]
            account_data["has_session"] = True
            acc_id = accounts_table.insert(account_data)
            userbot_clients[phone]["account_id"] = acc_id
            del userbot_clients[phone]["pending_data"]
        elif userbot_clients[phone].get("account_id"):
            accounts_table.update({"has_session": True}, Query().doc_id == userbot_clients[phone]["account_id"])
        
        userbot_clients[phone]["awaiting_code"] = False
        logging.info(f"Юзербот вошёл в {phone} (без пароля)")
        return True
    except SessionPasswordNeededError:
        password = None
        if userbot_clients[phone].get("pending_data"):
            password = userbot_clients[phone]["pending_data"].get("password")
        elif userbot_clients[phone].get("account_id"):
            account = accounts_table.get(doc_id=userbot_clients[phone]["account_id"])
            password = account.get("password") if account else None
        
        if password:
            try:
                await client.sign_in(password=password)
                
                if userbot_clients[phone].get("pending_data"):
                    account_data = userbot_clients[phone]["pending_data"]
                    account_data["has_session"] = True
                    acc_id = accounts_table.insert(account_data)
                    userbot_clients[phone]["account_id"] = acc_id
                    del userbot_clients[phone]["pending_data"]
                elif userbot_clients[phone].get("account_id"):
                    accounts_table.update({"has_session": True}, Query().doc_id == userbot_clients[phone]["account_id"])
                
                userbot_clients[phone]["awaiting_code"] = False
                logging.info(f"Юзербот вошёл в {phone} (с паролем)")
                return True
            except Exception as e:
                logging.error(f"Password error for {phone}: {e}")
                for admin_id in ADMIN_IDS:
                    await bot.send_message(
                        admin_id,
                        f"🔐 <b>Нужен облачный пароль для {phone}!</b>\n"
                        f"Отправь: <code>/pass {phone} пароль</code>",
                        parse_mode=ParseMode.HTML
                    )
                return False
        else:
            for admin_id in ADMIN_IDS:
                await bot.send_message(
                    admin_id,
                    f"🔐 <b>Нужен облачный пароль для {phone}!</b>\n"
                    f"Отправь: <code>/pass {phone} пароль</code>",
                    parse_mode=ParseMode.HTML
                )
            return False
    except Exception as e:
        logging.error(f"Account login error: {e}")
        if phone in userbot_clients:
            del userbot_clients[phone]
        return False

# ==================== АВТО-ПРОВЕРКА АККАУНТОВ ====================
async def check_accounts_health():
    while True:
        try:
            await asyncio.sleep(60)
            accounts = accounts_table.all()
            for account in accounts:
                phone = account.get("phone")
                doc_id = account.doc_id
                
                if phone in userbot_clients and userbot_clients[phone].get("awaiting_code"):
                    continue
                
                if phone not in userbot_clients:
                    accounts_table.remove(doc_ids=[doc_id])
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"⚠️ Аккаунт {phone} потерял доступ!\nУдалён из продажи автоматически."
                            )
                        except:
                            pass
                    continue
                
                client = userbot_clients[phone]["client"]
                try:
                    await client.connect()
                    me = await asyncio.wait_for(client.get_me(), timeout=15)
                    if me is None:
                        raise Exception("get_me вернул None")
                except Exception as e:
                    logging.warning(f"Аккаунт {phone} мёртв: {e} → удаляю из продажи")
                    accounts_table.remove(doc_ids=[doc_id])
                    if phone in userbot_clients:
                        try:
                            await userbot_clients[phone]["client"].disconnect()
                        except:
                            pass
                        del userbot_clients[phone]
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"⚠️ Аккаунт {phone} потерял доступ!\nУдалён из продажи автоматически."
                            )
                        except:
                            pass
        except Exception as e:
            logging.error(f"Health check error: {e}")

# ==================== КОМАНДА /pass ====================
@dp.message_handler(commands=['pass'])
async def set_password(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: <code>/pass +79991234567 пароль</code>", parse_mode=ParseMode.HTML)
        return
    
    phone = parts[1]
    password = parts[2]
    
    if phone not in userbot_clients:
        await message.answer("❌ Юзербот не найден для этого номера.")
        return
    
    client = userbot_clients[phone]["client"]
    
    try:
        await client.sign_in(password=password)
        
        if userbot_clients[phone].get("pending_data"):
            account_data = userbot_clients[phone]["pending_data"]
            account_data["has_session"] = True
            account_data["password"] = password
            acc_id = accounts_table.insert(account_data)
            userbot_clients[phone]["account_id"] = acc_id
            del userbot_clients[phone]["pending_data"]
        elif userbot_clients[phone].get("account_id"):
            accounts_table.update({"has_session": True, "password": password}, Query().doc_id == userbot_clients[phone]["account_id"])
        
        userbot_clients[phone]["awaiting_code"] = False
        await message.answer(f"✅ Облачный пароль для {phone} принят! Аккаунт готов к продаже.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ==================== ОБРАБОТЧИК КОДОВ ====================
@dp.message_handler(lambda msg: is_admin(msg.from_user.id) and msg.text and len(msg.text) == 5 and msg.text.isdigit())
async def catch_code(message: Message):
    if await check_ban_and_answer(message):
        return
    code = message.text
    
    if main_userbot and not await main_userbot.is_user_authorized():
        success = await login_userbot_with_code(code)
        if success:
            await message.answer("✅ Юзербот авторизован!")
            return
    
    awaiting_phones = [phone for phone, data in userbot_clients.items() if data.get("awaiting_code")]
    
    if not awaiting_phones:
        return
    
    phone = awaiting_phones[0]
    success = await login_account_userbot(phone, code)
    if success:
        await message.answer(f"✅ Вход для {phone} выполнен!")
    else:
        await message.answer(f"❌ Неверный код для {phone}. Попробуй ещё раз или отправь /pass {phone} пароль")

# ==================== /start ====================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    if await check_ban_and_answer(message):
        return
    
    args = message.get_args()
    referrer_id = None
    if args.startswith("ref_"):
        try:
            referrer_id = int(args.replace("ref_", ""))
        except:
            pass
    
    create_user_if_not(message.from_user.id, message.from_user.username, referrer_id)
    
    try:
        member = await bot.get_chat_member(CHANNEL_ID, message.from_user.id)
        if member.status in ['creator', 'administrator', 'member']:
            await show_main_menu(message.from_user.id)
        else:
            keyboard = InlineKeyboardMarkup(row_width=1)
            keyboard.add(InlineKeyboardButton("🔔 Подписаться", url=CHANNEL_LINK))
            keyboard.add(InlineKeyboardButton("♻️ Проверить подписку", callback_data="check_sub"))
            await bot.send_photo(message.from_user.id, "https://iili.io/BQyyE22.jpg", caption="<b>Для использования бота подпишитесь на канал!</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Start error: {e}")
        await message.answer(f"❌ Ошибка: {e}")

# ==================== CALLBACK: ПОДПИСКА ====================
@dp.callback_query_handler(text="check_sub")
async def check_subscription(call: CallbackQuery):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, call.from_user.id)
        if member.status in ['creator', 'administrator', 'member']:
            await call.message.delete()
            await show_main_menu(call.from_user.id)
        else:
            await call.answer("❌ Вы не подписаны!", show_alert=True)
    except:
        await call.answer("❌ Ошибка!")
    await call.answer()

# ==================== CALLBACK: ГЛАВНОЕ МЕНЮ ====================
@dp.callback_query_handler(text="main_menu")
async def back_to_main(call: CallbackQuery):
    try:
        await call.message.delete()
    except:
        pass
    await show_main_menu(call.from_user.id)
    await call.answer()

# ==================== ПАГИНАЦИЯ CALLBACK ====================
@dp.callback_query_handler(lambda call: call.data.startswith("page_"))
async def page_navigation(call: CallbackQuery):
    parts = call.data.split("_")
    acc_type = parts[1]
    page = int(parts[2])
    
    keyboard = get_country_keyboard_page(acc_type, page)
    if acc_type == "обычный":
        await call.message.edit_text("<b>Покупка аккаунта</b>\n\nВыберите страну:", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif acc_type == "отлега":
        await call.message.edit_text("<b>Аккаунты с отлегой</b>\n\nВыберите страну:", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

# ==================== CALLBACK: ПРОФИЛЬ ====================
@dp.callback_query_handler(text="profile")
async def show_profile(call: CallbackQuery):
    try:
        user = users_table.get(Query().user_id == call.from_user.id)
        
        if not user:
            await call.answer("❌ Вы не зарегистрированы. Напишите /start", show_alert=True)
            return
        
        balance = user.get('balance', 0)
        username = user.get('username', 'Неизвестный')
        purchases = user.get('purchases', 0)
        
        text = (
            "Профиль\n"
            "——————————————————\n"
            f"Имя пользователя: @{username}\n"
            f"Идентификатор: {call.from_user.id}\n"
            "——————————————————\n"
            f"👛 Баланс: {format_price(balance)}\n"
            f"Покупок: {purchases}"
        )
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("[👜] Пополнить баланс", callback_data="top_up"))
        keyboard.add(InlineKeyboardButton("[🎫] Промокод", callback_data="promo_code"))
        keyboard.add(InlineKeyboardButton("[🗄] Перевести деньги", callback_data="transfer_money"))
        keyboard.add(InlineKeyboardButton("[⛓️] Реф программа", callback_data="referral"))
        keyboard.add(InlineKeyboardButton("[🦺] Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}"))
        keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="main_menu"))
        await call.message.delete()
        await bot.send_photo(call.from_user.id, "https://iili.io/BZzNhN9.jpg", caption=text, reply_markup=keyboard)
        await call.answer()
    except Exception as e:
        logging.error(f"Profile error: {e}")
        await call.answer("❌ Ошибка загрузки профиля", show_alert=True)

# ==================== ПРОМОКОДЫ ====================
@dp.callback_query_handler(text="promo_code")
async def promo_code_start(call: CallbackQuery):
    await call.message.delete()
    await bot.send_message(call.from_user.id, "🎫 Введите промокод:")
    await PromoCode.waiting_promo.set()
    await call.answer()

@dp.message_handler(state=PromoCode.waiting_promo)
async def promo_code_check(message: Message, state: FSMContext):
    if await check_ban_and_answer(message):
        await state.finish()
        return
    
    code = message.text.strip().upper()
    promo = promocodes_table.get(Query().code == code)
    
    if not promo:
        await message.answer("❌ Промокод не найден.")
        await state.finish()
        return
    
    if promo.get("exhausted_at"):
        exhausted_time = datetime.fromisoformat(promo["exhausted_at"])
        if datetime.now() > exhausted_time + timedelta(minutes=5):
            promocodes_table.remove(Query().code == code)
            await message.answer("❌ Промокод уже недействителен.")
            await state.finish()
            return
        else:
            await message.answer("❌ Промокод уже использован.")
            await state.finish()
            return
    
    activations_left = promo.get("activations", 0)
    if activations_left <= 0:
        promocodes_table.update({"exhausted_at": datetime.now().isoformat()}, Query().code == code)
        await message.answer("❌ Промокод уже использован.")
        await state.finish()
        return
    
    amount = promo["amount"]
    user = get_user(message.from_user.id)
    new_balance = user["balance"] + amount
    update_balance(message.from_user.id, new_balance)
    
    new_activations = activations_left - 1
    if new_activations <= 0:
        promocodes_table.update({"activations": 0, "exhausted_at": datetime.now().isoformat()}, Query().code == code)
    else:
        promocodes_table.update({"activations": new_activations}, Query().code == code)
    
    await message.answer(f"✅ Промокод активирован! На баланс зачислено {format_price(amount)}.")
    await state.finish()

# ==================== ПЕРЕВОД ДЕНЕГ ====================
@dp.callback_query_handler(text="transfer_money")
async def transfer_money_start(call: CallbackQuery):
    user = get_user(call.from_user.id)
    await call.message.delete()
    text = (
        "<b>Перевод средств</b>\n\n"
        f"Доступно для перевода: {format_price(user.get('balance', 0))}\n\n"
        "Отправьте Telegram ID получателя (число).\n"
        "Узнать свой ID можно в профиле."
    )
    await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML)
    await TransferMoney.waiting_user_id.set()
    await call.answer()

@dp.message_handler(state=TransferMoney.waiting_user_id)
async def transfer_money_user_id(message: Message, state: FSMContext):
    if await check_ban_and_answer(message):
        await state.finish()
        return
    
    try:
        target_id = int(message.text.strip())
    except:
        await message.answer("❌ Введите корректный Telegram ID (число).")
        return
    
    if target_id == message.from_user.id:
        await message.answer("❌ Нельзя переводить деньги самому себе.")
        await state.finish()
        return
    
    target_user = get_user(target_id)
    if not target_user:
        await message.answer("❌ Пользователь с таким ID не найден в боте.")
        await state.finish()
        return
    
    await state.update_data(target_id=target_id)
    await message.answer(f"Введите сумму для перевода пользователю @{target_user.get('username', target_id)}:")
    await TransferMoney.waiting_amount.set()

@dp.message_handler(state=TransferMoney.waiting_amount)
async def transfer_money_amount(message: Message, state: FSMContext):
    if await check_ban_and_answer(message):
        await state.finish()
        return
    
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше нуля.")
            return
    except:
        await message.answer("❌ Введите корректную сумму.")
        return
    
    data = await state.get_data()
    target_id = data["target_id"]
    
    sender = get_user(message.from_user.id)
    receiver = get_user(target_id)
    
    if sender["balance"] < amount:
        await message.answer("❌ Недостаточно средств на балансе.")
        await state.finish()
        return
    
    update_balance(message.from_user.id, sender["balance"] - amount)
    update_balance(target_id, receiver["balance"] + amount)
    
    await message.answer(f"✅ Переведено {format_price(amount)} пользователю @{receiver.get('username', target_id)}.")
    
    try:
        await bot.send_message(
            target_id,
            f"💰 Вам перевели {format_price(amount)} от @{sender.get('username', message.from_user.id)}."
        )
    except:
        pass
    
    await state.finish()

# ==================== РЕФЕРАЛЬНАЯ ПРОГРАММА ====================
@dp.callback_query_handler(text="referral")
async def show_referral(call: CallbackQuery):
    ref_count = get_referral_count(call.from_user.id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{call.from_user.id}"
    
    text = (
        "<b>⛓️ Реферальная программа</b>\n\n"
        "<blockquote>Условия получения аккаунта: необходимо пригласить 15 уникальных пользователей. "
        "При условии, что один из приглашённых совершит покупку любого товара в боте, "
        "вы имеете право на получение любого аккаунта из ассортимента.</blockquote>\n\n"
        f"Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"📪 Вы пригласили: <b>{ref_count}</b> чел."
    )
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="profile"))
    
    await call.message.delete()
    await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

# ==================== ПОПОЛНЕНИЕ ====================
@dp.callback_query_handler(text="top_up")
async def top_up_menu(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        InlineKeyboardButton("[🧧] Подарками", url=GIFT_LINK),
        InlineKeyboardButton("[🔏] Crypto Bot", callback_data="topup_crypto")
    )
    keyboard.row(
        InlineKeyboardButton("[💎] GRAM", callback_data="topup_gram"),
        InlineKeyboardButton("[💵] USDT (TON)", callback_data="topup_usdt_ton")
    )
    keyboard.row(InlineKeyboardButton("[🪄] Звезды", callback_data="topup_stars"))
    keyboard.row(InlineKeyboardButton("[ ← Назад ]", callback_data="profile"))
    await call.message.delete()
    await bot.send_message(call.from_user.id, "<b>Выберите способ пополнения:</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(text="topup_crypto")
async def crypto_amount(call: CallbackQuery):
    await call.message.delete()
    await bot.send_message(call.from_user.id, "Введите сумму в рублях:")
    await TopUp.waiting_crypto_amount.set()
    await call.answer()

@dp.message_handler(state=TopUp.waiting_crypto_amount)
async def crypto_invoice(message: Message, state: FSMContext):
    if await check_ban_and_answer(message):
        await state.finish()
        return
    try:
        amount = Decimal(message.text.replace(",", "."))
        if amount < 10:
            await message.answer("Минимальная сумма: 10 ₽")
            return
        await message.answer("⏳ Создаю счёт...")
        invoice_url, invoice_id = await create_crypto_invoice(amount)
        if invoice_url:
            await async_safe_insert(pending_payments, {"user_id": message.from_user.id, "amount": float(amount), "invoice_id": invoice_id, "status": "pending", "created_at": datetime.now().isoformat()})
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("💳 Оплатить", url=invoice_url))
            keyboard.add(InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_crypto_{invoice_id}"))
            keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="top_up"))
            await message.answer(f"<b>Счёт на {format_price(amount)} создан!</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await message.answer("❌ Не удалось создать счёт.")
    except:
        await message.answer("❌ Введите корректную сумму!")
    finally:
        await state.finish()

@dp.callback_query_handler(lambda call: call.data.startswith("check_crypto_"))
async def check_crypto_payment_handler(call: CallbackQuery):
    invoice_id = call.data.replace("check_crypto_", "")
    invoice_data = await check_crypto_payment(invoice_id)
    
    logging.info(f"Проверка платежа {invoice_id}: {invoice_data}")
    
    if not invoice_data:
        await call.answer("❌ Чек не найден в Crypto Bot. Возможно, истёк.", show_alert=True)
        return
    
    status = invoice_data.get("status")
    
    if status == "paid":
        amount_usdt = float(invoice_data.get("amount", 0))
        rate = await get_usdt_rate_from_crypto_bot()
        if rate:
            amount_rub = float(Decimal(str(amount_usdt)) / rate)
        else:
            amount_rub = amount_usdt * 90
        
        user_id = call.from_user.id
        user = get_user(user_id)
        
        if user:
            new_balance = user["balance"] + amount_rub
            update_balance(user_id, new_balance)
            
            topup_history_table.insert({
                "user_id": user_id,
                "amount": amount_rub,
                "method": "Crypto Bot",
                "created_at": datetime.now().isoformat()
            })
            
            try:
                await call.message.delete()
            except:
                pass
            
            await bot.send_message(call.from_user.id, f"✅ Баланс пополнен на {format_price(amount_rub)}!")
            await call.answer("✅ Оплата прошла!")
        else:
            await call.answer("❌ Пользователь не найден!", show_alert=True)
    else:
        await call.answer(f"❌ Статус: {status}. Оплата ещё не прошла.", show_alert=True)
    
    await call.answer()

# ==================== GRAM ====================
@dp.callback_query_handler(text="topup_gram")
async def gram_start(call: CallbackQuery):
    await call.message.delete()
    await bot.send_message(
        call.from_user.id,
        "💎 <b>Введите сумму в рублях для пополнения через GRAM:</b>\n\n"
        "Минимум: 50 ₽",
        parse_mode=ParseMode.HTML
    )
    await TopUp.waiting_gram_amount.set()
    await call.answer()

@dp.message_handler(state=TopUp.waiting_gram_amount)
async def gram_invoice(message: Message, state: FSMContext):
    if await check_ban_and_answer(message):
        await state.finish()
        return
    
    try:
        amount_rub = float(message.text)
        if amount_rub < 50:
            await message.answer("Минимальная сумма: 50 ₽")
            return
        
        gram_rate = await get_gram_rate()
        amount_gram = float(Decimal(str(amount_rub)) / gram_rate)
        
        if amount_gram < 0.2:
            await message.answer(f"❌ Минимум 0.2 GRAM. По текущему курсу это ~{float(Decimal('0.2') * gram_rate):.0f} ₽")
            return
        
        code = generate_deposit_code()
        
        await async_safe_insert(pending_deposits, {
            "user_id": message.from_user.id,
            "amount_rub": amount_rub,
            "amount_crypto": amount_gram,
            "currency": "GRAM",
            "code": code,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        })
        
        keeper_link = create_tonkeeper_link(amount_gram, code)
        
        text = (
            f"💎 <b>Пополнение через GRAM</b>\n\n"
            f"Сумма: <code>{amount_gram:.4f} GRAM</code>\n"
            f"Уникальный комментарий:\n<code>{code}</code>\n\n"
            f"📤 Адрес для перевода:\n<code>{TON_WALLET}</code>\n\n"
            f"👛 Кнопка «Открыть в Keeper» подставит адрес, сумму и комментарий.\n"
            f"После отправки нажмите «Проверить оплату» или дождитесь автопроверки.\n\n"
            f"💰 Курс: 1 GRAM = {gram_rate:.2f} ₽\n"
            f"✅ Бот дополнительно проверяет оплату каждые 15 секунд\n\n"
            f"⚠️ <b>Важно:</b>\n"
            f"• Комментарий должен быть ТОЧНО: <code>{code}</code>\n"
            f"• Минимальная сумма: 0.2 GRAM\n"
            f"• После оплаты подождите 30 секунд"
        )
        
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("👛 Открыть в Keeper", url=keeper_link))
        keyboard.add(InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_gram_{code}"))
        keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="top_up"))
        
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except:
        await message.answer("❌ Введите корректную сумму!")
    finally:
        await state.finish()

@dp.callback_query_handler(lambda call: call.data.startswith("check_gram_"))
async def check_gram_payment(call: CallbackQuery):
    global TON_WALLET_RAW
    code = call.data.replace("check_gram_", "")
    deposit = pending_deposits.get(Query().code == code)
    
    if not deposit:
        await call.answer("❌ Платёж не найден!", show_alert=True)
        return
    
    if not TON_WALLET_RAW:
        TON_WALLET_RAW = convert_address_to_raw(TON_WALLET)
    
    try:
        url = f"https://toncenter.com/api/v3/transactions?account={TON_WALLET_RAW}&limit=50"
        headers = {"X-API-Key": TONCENTER_API_KEY}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                result = await resp.json()
                
                found = False
                tx_hash = ""
                for tx in result.get("transactions", []):
                    tx_hash = tx.get("hash", "")
                    if not tx_hash:
                        continue
                    
                    already = processed_transactions.get(Query().hash == tx_hash)
                    if already:
                        continue
                    
                    in_msg = tx.get("in_msg", {})
                    comment = extract_comment(in_msg)
                    raw_value = in_msg.get("value")
                    if raw_value is None:
                        continue
                    try:
                        value = int(raw_value)
                    except:
                        continue
                    
                    if comment == code and value > 0:
                        gram_amount = Decimal(value) / Decimal('1000000000')
                        if gram_amount < Decimal('0.2'):
                            continue
                        
                        needed_gram = Decimal(str(deposit["amount_crypto"]))
                        
                        if gram_amount < needed_gram:
                            await async_safe_remove(pending_deposits, Query().code == code)
                            await async_safe_insert(processed_transactions, {
                                "hash": tx_hash,
                                "code": code,
                                "processed_at": datetime.now().isoformat()
                            })
                            try:
                                await bot.send_message(
                                    deposit["user_id"],
                                    f"❌ <b>Недоплата!</b>\n\n"
                                    f"Вы отправили: <code>{gram_amount:.4f} GRAM</code>\n"
                                    f"Нужно было: <code>{needed_gram:.4f} GRAM</code>\n\n"
                                    f"Зачисление не выполнено. Обратитесь в поддержку.",
                                    parse_mode=ParseMode.HTML
                                )
                            except:
                                pass
                            await call.answer("❌ Недоплата! Зачисление не выполнено.", show_alert=True)
                            return
                        
                        found = True
                        break
                
                if found:
                    user = get_user(deposit["user_id"])
                    if user:
                        new_balance = user["balance"] + deposit["amount_rub"]
                        update_balance(deposit["user_id"], new_balance)
                        await async_safe_remove(pending_deposits, Query().code == code)
                        await async_safe_insert(processed_transactions, {
                            "hash": tx_hash,
                            "code": code,
                            "processed_at": datetime.now().isoformat()
                        })
                        
                        topup_history_table.insert({
                            "user_id": deposit["user_id"],
                            "amount": deposit["amount_rub"],
                            "method": "GRAM",
                            "created_at": datetime.now().isoformat()
                        })
                        
                        await call.message.delete()
                        await bot.send_message(call.from_user.id, f"✅ Баланс пополнен на {format_price(deposit['amount_rub'])}!")
                        await call.answer("✅ Оплата прошла!")
                        return
                
                await call.answer("Перевод с указанным комментарием пока не найден.", show_alert=True)
    except:
        await call.answer("❌ Ошибка проверки. Попробуйте позже.", show_alert=True)

# ==================== USDT (TON) ====================
@dp.callback_query_handler(text="topup_usdt_ton")
async def usdt_ton_start(call: CallbackQuery):
    await call.message.delete()
    await bot.send_message(
        call.from_user.id,
        "💵 <b>Введите сумму в рублях для пополнения через USDT (TON):</b>\n\n"
        "Минимум: 50 ₽",
        parse_mode=ParseMode.HTML
    )
    await TopUp.waiting_usdt_ton_amount.set()
    await call.answer()

@dp.message_handler(state=TopUp.waiting_usdt_ton_amount)
async def usdt_ton_invoice(message: Message, state: FSMContext):
    if await check_ban_and_answer(message):
        await state.finish()
        return
    
    try:
        amount_rub = float(message.text)
        if amount_rub < 50:
            await message.answer("Минимальная сумма: 50 ₽")
            return
        
        usdt_rate = await get_usdt_ton_rate()
        amount_usdt = float(Decimal(str(amount_rub)) / usdt_rate)
        
        if amount_usdt < 0.1:
            await message.answer(f"❌ Минимум 0.1 USDT. По текущему курсу это ~{float(Decimal('0.1') * usdt_rate):.0f} ₽")
            return
        
        code = generate_deposit_code()
        
        await async_safe_insert(pending_deposits, {
            "user_id": message.from_user.id,
            "amount_rub": amount_rub,
            "amount_crypto": amount_usdt,
            "currency": "USDT",
            "code": code,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        })
        
        keeper_link = create_tonkeeper_link(amount_usdt, code, jetton=USDT_CONTRACT_USERFRIENDLY)
        
        text = (
            f"💵 <b>Пополнение USDT в сети TON</b>\n\n"
            f"Сумма: <code>{amount_usdt:.4f} USDT</code>\n"
            f"Уникальный комментарий:\n<code>{code}</code>\n\n"
            f"📤 Адрес для перевода:\n<code>{TON_WALLET}</code>\n\n"
            f"👛 Кнопка «Открыть в Keeper» подставит все реквизиты.\n"
            f"Перед подтверждением проверьте, что выбран официальный USDT в сети TON, а не GRAM или другой токен.\n"
            f"Без точного комментария перевод не зачислится автоматически. Зачисляется фактически полученная сумма.\n\n"
            f"💰 Курс: 1 USDT = {usdt_rate:.2f} ₽\n"
            f"✅ Бот дополнительно проверяет оплату каждые 15 секунд\n\n"
            f"⚠️ <b>Важно:</b>\n"
            f"• Комментарий должен быть ТОЧНО: <code>{code}</code>\n"
            f"• Минимальная сумма: 0.1 USDT\n"
            f"• После оплаты подождите 30 секунд"
        )
        
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("👛 Открыть в Keeper", url=keeper_link))
        keyboard.add(InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_usdt_{code}"))
        keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="top_up"))
        
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except:
        await message.answer("❌ Введите корректную сумму!")
    finally:
        await state.finish()

@dp.callback_query_handler(lambda call: call.data.startswith("check_usdt_"))
async def check_usdt_payment(call: CallbackQuery):
    global TON_WALLET_RAW
    code = call.data.replace("check_usdt_", "")
    deposit = pending_deposits.get(Query().code == code)
    
    if not deposit:
        await call.answer("❌ Платёж не найден!", show_alert=True)
        return
    
    if not TON_WALLET_RAW:
        TON_WALLET_RAW = convert_address_to_raw(TON_WALLET)
    
    try:
        url = f"https://toncenter.com/api/v3/jetton/transfers?owner_address={TON_WALLET_RAW}&direction=in&limit=50"
        headers = {"X-API-Key": TONCENTER_API_KEY}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                result = await resp.json()
                
                found = False
                tx_hash = ""
                for transfer in result.get("jetton_transfers", []):
                    tx_hash = transfer.get("transaction_hash", "")
                    if not tx_hash:
                        continue
                    
                    already = processed_transactions.get(Query().hash == tx_hash)
                    if already:
                        continue
                    
                    jetton_master = transfer.get("jetton_master", "").lower()
                    if jetton_master != USDT_CONTRACT.lower():
                        continue
                    
                    forward_payload_b64 = transfer.get("forward_payload", "") or ""
                    if not forward_payload_b64:
                        continue
                    
                    try:
                        payload_bytes = base64.b64decode(forward_payload_b64)
                        if payload_bytes[:4] != b'\x00\x00\x00\x00':
                            continue
                        comment = payload_bytes[4:].decode('utf-8', errors='ignore').strip()
                    except:
                        continue
                    
                    if comment == code:
                        raw_amount = transfer.get("amount")
                        if raw_amount is None:
                            continue
                        try:
                            amount = int(raw_amount)
                        except:
                            continue
                        usdt_amount = Decimal(amount) / Decimal('1000000')
                        if usdt_amount < Decimal('0.1'):
                            continue
                        
                        needed_usdt = Decimal(str(deposit["amount_crypto"]))
                        
                        if usdt_amount < needed_usdt:
                            await async_safe_remove(pending_deposits, Query().code == code)
                            await async_safe_insert(processed_transactions, {
                                "hash": tx_hash,
                                "code": code,
                                "processed_at": datetime.now().isoformat()
                            })
                            try:
                                await bot.send_message(
                                    deposit["user_id"],
                                    f"❌ <b>Недоплата!</b>\n\n"
                                    f"Вы отправили: <code>{usdt_amount:.4f} USDT</code>\n"
                                    f"Нужно было: <code>{needed_usdt:.4f} USDT</code>\n\n"
                                    f"Зачисление не выполнено. Обратитесь в поддержку.",
                                    parse_mode=ParseMode.HTML
                                )
                            except:
                                pass
                            await call.answer("❌ Недоплата! Зачисление не выполнено.", show_alert=True)
                            return
                        
                        found = True
                        break
                
                if found:
                    user = get_user(deposit["user_id"])
                    if user:
                        new_balance = user["balance"] + deposit["amount_rub"]
                        update_balance(deposit["user_id"], new_balance)
                        await async_safe_remove(pending_deposits, Query().code == code)
                        await async_safe_insert(processed_transactions, {
                            "hash": tx_hash,
                            "code": code,
                            "processed_at": datetime.now().isoformat()
                        })
                        
                        topup_history_table.insert({
                            "user_id": deposit["user_id"],
                            "amount": deposit["amount_rub"],
                            "method": "USDT (TON)",
                            "created_at": datetime.now().isoformat()
                        })
                        
                        await call.message.delete()
                        await bot.send_message(call.from_user.id, f"✅ Баланс пополнен на {format_price(deposit['amount_rub'])}!")
                        await call.answer("✅ Оплата прошла!")
                        return
                
                await call.answer("Перевод USDT с вашим комментарием пока не найден.", show_alert=True)
    except:
        await call.answer("❌ Ошибка проверки. Попробуйте позже.", show_alert=True)

# ==================== STARS ====================
@dp.callback_query_handler(text="topup_stars")
async def stars_amount(call: CallbackQuery):
    await call.message.delete()
    await bot.send_message(
        call.from_user.id,
        f"<b>Введите сумму в рублях для пополнения через Stars:</b>\n\n"
        f"⚠️ <b>ВАЖНО:</b> отправьте любое слово боту @{STARS_BOT_USERNAME}, "
        f"а затем введите сумму для пополнения.\n\n"
        f"<blockquote>💡 Если у вас не хватает звёзд или их нет совсем — вы можете купить их через ботов в Telegram за пару кликов. Просто введите в поиске Telegram: «купить звёзды» и следуйте инструкциям.\n\n"
        f"⚠️ Мы не отвечаем за ваши действия и покупки у других продавцов. Мы отвечаем только за свой проект. Это лишь подсказка, где можно найти звёзды по низкой цене.</blockquote>",
        parse_mode=ParseMode.HTML
    )
    await TopUp.waiting_stars_amount.set()
    await call.answer()

@dp.message_handler(state=TopUp.waiting_stars_amount)
async def stars_invoice(message: Message, state: FSMContext):
    if await check_ban_and_answer(message):
        await state.finish()
        return
    try:
        amount_rub = float(message.text)
        if amount_rub < 10:
            await message.answer("Минимальная сумма: 10 руб.")
            return
        amount_stars = int(amount_rub / 0.71)
        
        try:
            await second_bot.send_message(message.from_user.id, f"💫 Счёт на {amount_stars} Stars")
            await asyncio.sleep(0.3)
        except:
            pass
        
        await second_bot.send_invoice(
            chat_id=message.from_user.id,
            title="Пополнение баланса",
            description=f"Пополнение на {format_price(amount_rub)}",
            payload=f"topup_{message.from_user.id}_{amount_rub}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Пополнение", amount=amount_stars)]
        )
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🤖 Перейти к оплате", url=f"https://t.me/{STARS_BOT_USERNAME}"))
        
        await message.answer(
            f"⭐ Счёт на {amount_stars} Stars создан!\n\n"
            f"<b>Сумма пополнения:</b> {format_price(amount_rub)}\n\n"
            f"<b>Для оплаты:</b>\n"
            f"1. Нажмите кнопку ниже и перейдите в чат с ботом.\n"
            f"2. Нажмите <b>Оплатить</b> под сообщением со счётом.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        await state.finish()

# ==================== ОБРАБОТЧИКИ ВТОРОГО БОТА ====================
@dp_second.pre_checkout_query_handler(lambda query: True)
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await second_bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp_second.message_handler(content_types=ContentTypes.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    parts = payload.split('_')
    user_id = int(parts[1])
    amount_rub = float(parts[2])
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "http://localhost:8080/topup",
                json={"user_id": user_id, "amount": amount_rub, "secret": API_SECRET}
            ) as resp:
                result = await resp.json()
                if result["status"] == "ok":
                    await message.answer("✅ Оплата получена! Баланс пополнен.")
                else:
                    await message.answer(f"❌ Ошибка: {result.get('message')}")
        except Exception as e:
            await message.answer(f"❌ Ошибка связи: {e}")

# ==================== ПОКУПКА ====================
@dp.callback_query_handler(text="buy_regular")
async def buy_regular(call: CallbackQuery):
    keyboard = get_country_keyboard_page("обычный", 0)
    await call.message.delete()
    await bot.send_message(call.from_user.id, "<b>Покупка аккаунта</b>\n\nВыберите страну:", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(text="buy_aged")
async def buy_aged(call: CallbackQuery):
    accounts = accounts_table.search(Query().acc_type == "отлега")
    if not accounts:
        await call.answer("❌ Нет доступных!", show_alert=True)
        return
    keyboard = get_country_keyboard_page("отлега", 0)
    await call.message.delete()
    await bot.send_message(call.from_user.id, "<b>Аккаунты с отлегой</b>\n\nВыберите страну:", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(lambda call: call.data.startswith("aged_country_"))
async def aged_country_select(call: CallbackQuery):
    country_code = call.data.replace("aged_country_", "")
    keyboard = get_years_keyboard(country_code)
    if not keyboard.inline_keyboard:
        await call.answer("❌ Нет годов!", show_alert=True)
        return
    await call.message.delete()
    await bot.send_message(call.from_user.id, "<b>Выберите год:</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(lambda call: call.data.startswith("country_"))
async def country_select(call: CallbackQuery):
    parts = call.data.split("_")
    acc_type = parts[1]
    country_code = parts[2]
    account = get_available_account(acc_type, country_code)
    if not account:
        await call.answer("❌ Нет доступных!", show_alert=True)
        keyboard = get_country_keyboard_page(acc_type, 0)
        try:
            await call.message.edit_text("<b>Выберите страну:</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except:
            pass
        return
    
    year_text = ""
    if acc_type == "отлега" and account.get("year"):
        year_text = f" {account.get('year')} года"
    
    text = (
        f"<b>{'Обычный аккаунт' if acc_type == 'обычный' else 'Аккаунт с отлегой'}{year_text}</b>\n\n"
        f"📍 Страна: {account['country_flag']} {account['country_name']}\n"
        f"💵 Цена: {format_price(account['price'])}"
    )
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("💰 Купить", callback_data=f"purchase_{account.doc_id}"))
    keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data=f"page_{acc_type}_0"))
    try:
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except:
        await call.message.delete()
        await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(lambda call: call.data.startswith("year_"))
async def year_select(call: CallbackQuery):
    parts = call.data.split("_")
    country_code = parts[1]
    year = parts[2]
    account = get_available_account("отлега", country_code, year)
    if not account:
        await call.answer("❌ Нет доступных!", show_alert=True)
        keyboard = get_years_keyboard(country_code)
        try:
            await call.message.edit_text("<b>Выберите год:</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except:
            pass
        return
    text = (
        f"<b>Аккаунт с отлегой {year} года</b>\n\n"
        f"📍 Страна: {account['country_flag']} {account['country_name']}\n"
        f"📅 Год: {year}\n"
        f"💵 Цена: {format_price(account['price'])}"
    )
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("💰 Купить", callback_data=f"purchase_{account.doc_id}"))
    keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data=f"aged_country_{country_code}"))
    try:
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except:
        await call.message.delete()
        await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(lambda call: call.data.startswith("purchase_"))
async def purchase_account(call: CallbackQuery):
    account_id = int(call.data.replace("purchase_", ""))
    account = accounts_table.get(doc_id=account_id)
    user = get_user(call.from_user.id)
    if not account:
        await call.answer("❌ Не найден!", show_alert=True)
        return
    if user["balance"] < account["price"]:
        await call.answer("❌ Недостаточно средств!", show_alert=True)
        return
    new_balance = user["balance"] - account["price"]
    update_balance(call.from_user.id, new_balance)
    users_table.update({"purchases": user["purchases"] + 1}, Query().user_id == call.from_user.id)
    
    phone = account["phone"]
    price = account["price"]
    country_flag = account["country_flag"]
    country_name = account["country_name"]
    password = account.get("password")
    
    user_data = get_user(call.from_user.id)
    if user_data and user_data.get("referrer_id"):
        referrals_table.update(
            {"has_purchased": True},
            (Query().user_id == user_data["referrer_id"]) & (Query().invited_user_id == call.from_user.id)
        )
    
    purchase_history_table.insert({
        "user_id": call.from_user.id,
        "phone": phone,
        "country": country_name,
        "price": price,
        "created_at": datetime.now().isoformat()
    })
    
    orders_table.insert({"phone": phone, "buyer_id": call.from_user.id, "status": "ожидает_код", "created_at": datetime.now().isoformat()})
    accounts_table.remove(doc_ids=[account_id])
    sold_accounts_table.insert({**account, "buyer_id": call.from_user.id, "sold_at": datetime.now().isoformat()})
    await call.message.delete()
    
    result_text = (
        f"✅ <b>Аккаунт куплен!</b>\n\n"
        f"📱 Номер: <code>{phone}</code>\n"
        f"🌍 Страна: {country_flag} {country_name}\n"
        f"💰 Цена: {format_price(price)}\n\n"
        f"🔑 <b>Введите номер в Telegram.</b>\n"
        f"Код подтверждения придёт автоматически."
    )
    if password:
        result_text += f"\n\n🔐 <b>Облачный пароль:</b> <code>{password}</code>"
    
    await bot.send_message(call.from_user.id, result_text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard(call.from_user.id))
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"💰 Продан {phone}\n👤 @{call.from_user.username}\n💵 {format_price(price)}")
        except:
            pass
    await call.answer("✅ Куплен!", show_alert=True)

# ==================== АДМИН-ПАНЕЛЬ ====================
@dp.callback_query_handler(text="admin_panel")
async def admin_panel(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Нет доступа!", show_alert=True)
        return
    await call.message.delete()
    await bot.send_message(call.from_user.id, "<b>🛠 Админ-панель</b>", parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())
    await call.answer()

@dp.callback_query_handler(text="admin_add")
async def admin_add_start(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.message.delete()
    await bot.send_message(call.from_user.id, "<b>➕ Добавление аккаунта</b>\n\nВведите страну с флагом (например: 🇺🇸США):", parse_mode=ParseMode.HTML)
    await AddAccount.waiting_country.set()
    await call.answer()

@dp.message_handler(state=AddAccount.waiting_country)
async def admin_add_country(message: Message, state: FSMContext):
    await state.update_data(country=message.text)
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(InlineKeyboardButton("Обычный", callback_data="type_обычный"), InlineKeyboardButton("Отлега", callback_data="type_отлега"))
    await message.answer("Выберите тип:", reply_markup=keyboard)
    await AddAccount.waiting_type.set()

@dp.callback_query_handler(lambda call: call.data.startswith("type_"), state=AddAccount.waiting_type)
async def admin_add_type(call: CallbackQuery, state: FSMContext):
    acc_type = call.data.replace("type_", "")
    await state.update_data(acc_type=acc_type)
    if acc_type == "отлега":
        await call.message.delete()
        await bot.send_message(call.from_user.id, "Введите год регистрации:")
        await AddAccount.waiting_year.set()
    else:
        await call.message.delete()
        await bot.send_message(call.from_user.id, "Введите цену в рублях:")
        await AddAccount.waiting_price.set()
    await call.answer()

@dp.message_handler(state=AddAccount.waiting_year)
async def admin_add_year(message: Message, state: FSMContext):
    try:
        year = int(message.text)
        await state.update_data(year=year)
        await message.answer("Введите цену в рублях:")
        await AddAccount.waiting_price.set()
    except:
        await message.answer("❌ Введите год цифрами!")

@dp.message_handler(state=AddAccount.waiting_price)
async def admin_add_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        await state.update_data(price=price)
        await message.answer("Введите номер телефона:")
        await AddAccount.waiting_phone.set()
    except:
        await message.answer("❌ Введите цену цифрами!")

@dp.message_handler(state=AddAccount.waiting_phone)
async def admin_add_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    phone = message.text
    country_input = data["country"]
    if len(country_input) >= 2 and ord(country_input[0]) > 127:
        flag = country_input[:2]
        name = country_input[2:].strip()
    else:
        flag = ""
        name = country_input
    await state.update_data(flag=flag, name=name, phone=phone)
    await message.answer("Введите облачный пароль (или напишите <b>нет</b> если его нет):", parse_mode=ParseMode.HTML)
    await AddAccount.waiting_password.set()

@dp.message_handler(state=AddAccount.waiting_password)
async def admin_add_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if password.lower() == "нет":
        password = None
    
    data = await state.get_data()
    await state.update_data(password=password)
    
    phone = data["phone"]
    
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
        
        userbot_clients[phone] = {
            "client": client,
            "account_id": None,
            "awaiting_code": True,
            "pending_data": {
                "country_name": data["name"],
                "country_flag": data["flag"],
                "country_code": data["name"],
                "acc_type": data["acc_type"],
                "price": data["price"],
                "phone": phone,
                "password": password,
                "has_session": False,
                "added_at": datetime.now().isoformat(),
                "year": data.get("year")
            }
        }
        
        await message.answer(
            f"📱 Номер: {phone}\n🔐 <b>Введи код подтверждения:</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки кода: {e}")
        await state.finish()
        return
    
    await AddAccount.waiting_code.set()

@dp.message_handler(state=AddAccount.waiting_code)
async def admin_add_code(message: Message, state: FSMContext):
    data = await state.get_data()
    phone = data["phone"]
    code = message.text
    success = await login_account_userbot(phone, code)
    if success:
        await message.answer(
            f"✅ Аккаунт {phone} готов!\n"
            f"🏳️ Страна: {data.get('flag', '')} {data.get('name', '')}\n"
            f"📦 Тип: {data.get('acc_type', '')}\n"
            f"💵 Цена: {format_price(data.get('price', 0))}"
            + (f"\n🔐 Пароль: {data.get('password', 'нет')}" if data.get("password") else "")
        )
        await state.finish()
    else:
        await message.answer(f"❌ Неверный код. Попробуй ещё раз или отправь /pass {phone} пароль")

@dp.callback_query_handler(text="admin_stock")
async def admin_stock(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    accounts = accounts_table.all()
    text = "<b>📊 Остатки:</b>\n\n"
    if not accounts:
        text += "Нет доступных."
    else:
        for country in set(a["country_name"] for a in accounts):
            flag_data = accounts_table.get(Query().country_name == country)
            flag = flag_data["country_flag"] if flag_data else ""
            regular = len(accounts_table.search((Query().country_name == country) & (Query().acc_type == "обычный")))
            aged = len(accounts_table.search((Query().country_name == country) & (Query().acc_type == "отлега")))
            text += f"{flag} {country}: {regular} обычных, {aged} с отлегой\n"
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("[ ← Назад ]", callback_data="admin_panel"))
    await call.message.delete()
    await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(text="admin_broadcast")
async def admin_broadcast(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.message.delete()
    await bot.send_message(call.from_user.id, "Введите текст для рассылки:")
    await Broadcast.waiting_text.set()
    await call.answer()

@dp.message_handler(state=Broadcast.waiting_text, content_types=ContentTypes.ANY)
async def broadcast_send(message: Message, state: FSMContext):
    users = users_table.all()
    success = 0
    failed = 0
    for user in users:
        try:
            await message.copy_to(user["user_id"])
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Рассылка завершена!\nУспешно: {success}\nНеудачно: {failed}")
    await state.finish()

@dp.callback_query_handler(text="admin_topup_user")
async def admin_topup_user(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.message.delete()
    await bot.send_message(call.from_user.id, "Введите ID и сумму:\n<code>123456789 100</code>", parse_mode=ParseMode.HTML)
    
    @dp.message_handler(lambda msg: is_admin(msg.from_user.id) and len(msg.text.split()) == 2)
    async def process_topup(msg: Message):
        try:
            parts = msg.text.split()
            user_id = int(parts[0])
            amount = float(parts[1])
            user = get_user(user_id)
            if user:
                new_balance = user["balance"] + amount
                update_balance(user_id, new_balance)
                topup_history_table.insert({
                    "user_id": user_id,
                    "amount": amount,
                    "method": "Админ",
                    "created_at": datetime.now().isoformat()
                })
                await msg.answer(f"✅ Баланс {user_id} пополнен на {format_price(amount)}")
            else:
                await msg.answer("❌ Не найден!")
        except:
            await msg.answer("❌ Неверный формат!")
    await call.answer()

# ==================== АДМИН: РЕФЕРАЛЫ ====================
@dp.callback_query_handler(text="admin_referrals")
async def admin_referrals(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    
    all_refs = referrals_table.all()
    if not all_refs:
        await call.answer("Нет рефералов", show_alert=True)
        return
    
    stats = {}
    for ref in all_refs:
        uid = ref["user_id"]
        if uid not in stats:
            stats[uid] = {"total": 0, "purchased": 0}
        stats[uid]["total"] += 1
        if ref.get("has_purchased"):
            stats[uid]["purchased"] += 1
    
    text = "<b>📋 Статистика рефералов:</b>\n\n"
    for uid, data in sorted(stats.items(), key=lambda x: x[1]["total"], reverse=True):
        user = get_user(uid)
        username = user["username"] if user else "Неизвестный"
        text += f"@{username} (ID: {uid})\nПригласил: {data['total']} | Купили: {data['purchased']}\n\n"
    
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("[ ← Назад ]", callback_data="admin_panel"))
    await call.message.delete()
    await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

# ==================== АДМИН: БАН ====================
@dp.callback_query_handler(text="admin_ban")
async def admin_ban_start(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    
    await call.message.delete()
    await bot.send_message(call.from_user.id, "Введите ID или @username для бана:")
    await BanUser.waiting_ban_id.set()
    await call.answer()

@dp.message_handler(state=BanUser.waiting_ban_id)
async def admin_ban_user(message: Message, state: FSMContext):
    target = message.text.strip()
    
    user = None
    if target.startswith("@"):
        user = users_table.get(Query().username == target[1:])
    else:
        try:
            uid = int(target)
            user = get_user(uid)
        except:
            pass
    
    if not user:
        await message.answer("❌ Пользователь не найден в базе.")
        await state.finish()
        return
    
    uid = user["user_id"]
    
    if not is_banned(uid):
        banned_table.insert({"user_id": uid, "banned_at": datetime.now().isoformat()})
        await message.answer(f"🚫 Пользователь {uid} забанен.")
        try:
            await bot.send_message(uid, "🚫 Ваш тикет закрыт.")
        except:
            pass
    else:
        banned_table.remove(Query().user_id == uid)
        await message.answer(f"✅ Пользователь {uid} разбанен.")
    
    await state.finish()

# ==================== АДМИН: ПРОМОКОДЫ ====================
@dp.callback_query_handler(text="admin_promo")
async def admin_promo_start(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    
    await call.message.delete()
    await bot.send_message(
        call.from_user.id,
        "🎫 <b>Создание промокода</b>\n\nВведите данные в формате:\n<code>КОД СУММА АКТИВАЦИИ</code>\n\nПример: <code>START50 50 10</code>",
        parse_mode=ParseMode.HTML
    )
    
    @dp.message_handler(lambda msg: is_admin(msg.from_user.id) and len(msg.text.split()) == 3)
    async def process_promo(msg: Message):
        try:
            parts = msg.text.split()
            code = parts[0].upper()
            amount = float(parts[1])
            activations = int(parts[2])
            
            existing = promocodes_table.get(Query().code == code)
            if existing:
                await msg.answer("❌ Промокод с таким названием уже существует.")
                return
            
            promocodes_table.insert({
                "code": code,
                "amount": amount,
                "activations": activations,
                "created_at": datetime.now().isoformat(),
                "exhausted_at": None
            })
            
            await msg.answer(
                f"✅ Промокод <b>{code}</b> создан!\n"
                f"💰 Сумма: {format_price(amount)}\n"
                f"👥 Активаций: {activations}",
                parse_mode=ParseMode.HTML
            )
        except:
            await msg.answer("❌ Неверный формат!")
    
    await call.answer()

# ==================== АДМИН: ПОЛЬЗОВАТЕЛИ ====================
@dp.callback_query_handler(text="admin_users")
async def admin_users_start(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    
    users = users_table.all()
    total = len(users)
    
    text = f"<b>👥 Всего пользователей: {total}</b>\n\nВыберите пользователя:"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    for user in users[:50]:
        uid = user.get("user_id")
        username = user.get("username", "Неизвестный")
        keyboard.insert(InlineKeyboardButton(
            f"@{username}",
            callback_data=f"user_info_{uid}"
        ))
    keyboard.add(InlineKeyboardButton("[ ← Назад ]", callback_data="admin_panel"))
    
    await call.message.delete()
    await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

@dp.callback_query_handler(lambda call: call.data.startswith("user_info_"))
async def admin_user_info(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    
    uid = int(call.data.replace("user_info_", ""))
    user = get_user(uid)
    
    if not user:
        await call.answer("❌ Не найден!", show_alert=True)
        return
    
    topups = topup_history_table.search(Query().user_id == uid)
    purchases = purchase_history_table.search(Query().user_id == uid)
    
    total_topup = sum(t.get("amount", 0) for t in topups)
    
    text = (
        f"👤 <b>@{user.get('username', 'Неизвестный')}</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"💰 Баланс: {format_price(user.get('balance', 0))}\n"
        f"💵 Всего пополнено: {format_price(total_topup)}\n"
        f"📥 Пополнений: {len(topups)}\n"
        f"🛒 Покупок: {len(purchases)}\n\n"
    )
    
    if purchases:
        text += "<b>📦 История покупок:</b>\n"
        for p in purchases[-10:]:
            text += f"• <code>{p.get('phone')}</code> ({p.get('country')}) — {format_price(p.get('price', 0))}\n"
        text += "\n"
    
    if topups:
        text += "<b>💳 История пополнений:</b>\n"
        for t in topups[-10:]:
            text += f"• {format_price(t.get('amount', 0))} — {t.get('method', '?')}\n"
    
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("[ ← Назад ]", callback_data="admin_users"))
    await call.message.delete()
    await bot.send_message(call.from_user.id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await call.answer()

# ==================== ЗАПУСК ====================
async def on_startup(dp):
    logging.info("Запуск...")
    
    global TON_WALLET_RAW
    TON_WALLET_RAW = convert_address_to_raw(TON_WALLET)
    logging.info(f"TON_WALLET_RAW: {TON_WALLET_RAW}")
    
    success = await start_main_userbot()
    if success:
        logging.info("Юзербот авторизован")
    else:
        logging.info("Ожидание кода...")
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()
    logging.info("HTTP сервер запущен на порту 8080")
    
    asyncio.create_task(check_accounts_health())
    asyncio.create_task(check_ton_transactions())

async def main():
    await on_startup(dp)
    await asyncio.gather(
        dp.start_polling(),
        dp_second.start_polling()
    )

if __name__ == '__main__':
    asyncio.run(main())