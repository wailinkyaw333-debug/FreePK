import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from datetime import datetime, timedelta, timezone

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "YOUR_GITHUB_TOKEN_HERE")
REPO_OWNER = os.getenv("REPO_OWNER", "YOUR_GITHUB_USERNAME")
REPO_NAME = os.getenv("REPO_NAME", "FreeBotPK")
ADMIN_ID = os.getenv("ADMIN_ID", "YOUR_TELEGRAM_USER_ID")

ADMINS = [ADMIN_ID]
# ================================================

def is_admin(user_id):
    return str(user_id) in ADMINS

bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_texts = {}
limited_texts = {}
session = None
_connector = None
_start_time = time.monotonic()

async def web_server():
    app = web.Application()
    app.router.add_get('/', lambda request: web.Response(text="Bot is awake!"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"✅ Web server started on port {port}")

async def get_file_content(path):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with session.get(url, headers=headers) as response:
        if response.status == 200:
            data = await response.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            return json.loads(content), data['sha']
    return {}, None

async def update_file_content(path, content, sha, message):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    encoded = base64.b64encode(json.dumps(content).encode()).decode()
    payload = {
        "message": message,
        "content": encoded,
        "sha": sha
    }
    async with session.put(url, headers=headers, json=payload) as response:
        return await response.text()

def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🎫 PAID USER", callback_data="menu_paid"),
        InlineKeyboardButton("🔗 Portal URL ထည့်ရန်", callback_data="menu_free_trial"),
        InlineKeyboardButton("📋 Success Codes", callback_data="menu_result"),
        InlineKeyboardButton("🔄 Recheck", callback_data="menu_recheck"),
        InlineKeyboardButton("🛑 Stop Scan", callback_data="menu_stop"),
        InlineKeyboardButton("🔙 Back", callback_data="menu_back")
    )
    return keyboard

def get_back_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="menu_back"))
    return keyboard

@bot.message_handler(commands=['start'])
async def start(message):
    user_id = str(message.chat.id)
    user_name = message.from_user.first_name or "User"
    
    if user_id in approve:
        text = f"✨ Welcome {user_name}!\n✅ PAID USER\n🆔 ID: {user_id}"
    else:
        text = f"✨ Welcome {user_name}!\n🆔 ID: {user_id}\n⚠️ Not registered. Contact Admin."
    
    await bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if not is_admin(message.chat.id):
        return
    args = message.text.split()
    if len(args) < 3:
        await bot.reply_to(message, "Usage: /genkey unlimited 123456789")
        return
    user_id = args[2]
    auth_list, sha = await get_file_content("auth_list.json")
    auth_list[user_id] = {"plan": "unlimited", "expires_at": "9999-12-31T23:59:59Z"}
    await update_file_content("auth_list.json", auth_list, sha, f"Add key for {user_id}")
    await bot.reply_to(message, f"✅ Key added for {user_id}")

@bot.message_handler(commands=['portal'])
async def set_portal(message):
    user_id = str(message.chat.id)
    if user_id not in approve:
        await bot.reply_to(message, "❌ You are not a PAID USER.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /portal your_portal_url")
        return
    portal_url = args[1]
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['session_url'] = portal_url
    await bot.reply_to(message, f"✅ Portal URL saved:\n{portal_url}")

@bot.message_handler(commands=['brute'])
async def brute(message):
    user_id = str(message.chat.id)
    if user_id not in approve:
        await bot.reply_to(message, "❌ You are not a PAID USER.")
        return
    if user_id not in user_data or 'session_url' not in user_data[user_id]:
        await bot.reply_to(message, "❌ Please set Portal URL first: /portal your_url")
        return
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /brute <mode>\nModes: 6, 7, 8, 9, ascii-lower, all")
        return
    mode = args[1]
    portal_url = user_data[user_id]['session_url']
    
    # Simple brute force simulation (replace with actual logic)
    await bot.reply_to(message, f"🔍 Starting brute force for mode: {mode}")
    # Actual brute force logic would go here
    # For demo, just send a dummy response
    await bot.reply_to(message, f"✅ Brute force completed for mode: {mode} (simulated)")

@bot.callback_query_handler(func=lambda call: True)
async def callback_handler(call):
    chat_id = call.message.chat.id
    user_id = str(chat_id)
    
    if call.data == "menu_back":
        await bot.edit_message_text("Main Menu", chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_keyboard())
        await bot.answer_callback_query(call.id)
        return
    
    if call.data == "menu_paid":
        await bot.edit_message_text("Contact admin to buy key.", chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_back_keyboard())
        await bot.answer_callback_query(call.id)
        return
    
    if call.data == "menu_free_trial":
        await bot.edit_message_text("Send /portal [your_portal_url]", chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_back_keyboard())
        await bot.answer_callback_query(call.id)
        return
    
    if call.data == "menu_result":
        results, _ = await get_file_content("result.json")
        codes = results.get(user_id, [])
        text = "\n".join(codes) if codes else "No codes found."
        await bot.edit_message_text(f"📋 Your Codes:\n{text}", chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_back_keyboard())
        await bot.answer_callback_query(call.id)
        return
    
    if call.data == "menu_recheck":
        await bot.edit_message_text("Rechecking...", chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_back_keyboard())
        await bot.answer_callback_query(call.id)
        return
    
    if call.data == "menu_stop":
        await bot.edit_message_text("Scan stopped.", chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_back_keyboard())
        await bot.answer_callback_query(call.id)
        return
    
    await bot.answer_callback_query(call.id)

async def main():
    global session, _connector
    timeout = aiohttp.ClientTimeout(total=30)
    _connector = aiohttp.TCPConnector(limit=1000, ttl_dns_cache=300, ssl=True)
    session = aiohttp.ClientSession(timeout=timeout, connector=_connector, connector_owner=False)
    try:
        asyncio.create_task(web_server())
        await bot.polling(timeout=20, request_timeout=35, non_stop=True)
    finally:
        await session.close()
        await _connector.close()

if __name__ == '__main__':
    asyncio.run(main())
