import os
import json
import asyncio
import aiohttp
from datetime import datetime
from flask import Flask
from threading import Thread
from telegram import Update, MessageEntity
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------------------------------------------------------------------
# Keep-alive web server (Render / Replit style)
# ---------------------------------------------------------------------------
app_web = Flask('')


@app_web.route('/')
def home():
    return "Bot Alive!"


def run_web():
    app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))


Thread(target=run_web).start()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = 5747820322

OWL_AK = "RMwMg5ff4GcBondlIV71XrNIlUHwRME2"
OWL_SK = "4QCN0Ln9014DliUp4n8PXECq"

USERS_FILE = "users.json"
MAX_CONCURRENT_CHECKS = 25

TEST_URL_PRIMARY = "http://cp.cloudflare.com/generate_204"
TEST_URL_FALLBACK_US = "http://www.gstatic.com/generate_204"

# Telegram Premium custom emoji IDs (used only on the final summary message)
CUSTOM_EMOJI = {
    "clipboard": "6107258420476255796",  # 📋
    "green": "6104838068966005855",      # 🟢
    "red": "6203979615103358663",        # 🔴
    "recycle": "6104798108590285245",    # 🔄
    "chart": "6206343625232619150",      # 📈
}

WELCOME_TEXT = (
    "👋 Welcome to the Owl Proxy Checker Bot!\n\n"
    "Send me a list of proxies, or upload a .txt file, and I will check them "
    "concurrently for you. I will return a summary along with a clean .txt "
    "file containing only the working (live) proxies.\n\n"
    "📝 Supported Formats:\n"
    "• host:port\n"
    "• host:port:user:pass\n"
    "• user:pass@host:port\n\n"
    "💡 Just paste your proxies here or drag-and-drop a .txt file to start!"
)

# ---------------------------------------------------------------------------
# User persistence (for admin panel / broadcast)
# ---------------------------------------------------------------------------


def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(list(users), f)


USERS = load_users()


def register_user(user_id: int):
    if user_id not in USERS:
        USERS.add(user_id)
        save_users(USERS)


# ---------------------------------------------------------------------------
# Custom-emoji message builder (Telegram MessageEntity offsets are UTF-16
# code-unit based, so we track offsets manually)
# ---------------------------------------------------------------------------


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def build_emoji_message(parts):
    """
    parts: list of (text_segment, emoji_key_or_None)
    If emoji_key is given, text_segment is the emoji placeholder character
    itself and a custom_emoji entity is attached covering it.
    """
    text = ""
    entities = []
    for segment, emoji_key in parts:
        offset = utf16_len(text)
        text += segment
        if emoji_key:
            entities.append(
                MessageEntity(
                    type=MessageEntity.CUSTOM_EMOJI,
                    offset=offset,
                    length=utf16_len(segment),
                    custom_emoji_id=CUSTOM_EMOJI[emoji_key],
                )
            )
    return text, entities


def build_progress_message(done, total, live, dead):
    width = 20
    filled = int(width * done / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * done / total) if total else 0
    text = (
        f"⚡ Checking proxies in progress...\n\n"
        f"Progress: [{bar}] {pct}%\n\n"
        f"📊 Checked: {done} / {total}\n"
        f"🟢 Working (Live): {live}\n"
        f"🔴 Dead: {dead}"
    )
    return text


def build_summary_message(total, live, dead, fallback):
    success_rate = (live / total * 100) if total else 0
    text, entities = build_emoji_message(
        [
            ("✅ Proxy Check Complete\n\n", None),
            ("📋", "clipboard"),
            (f" Total checked: {total}\n", None),
            ("🟢", "green"),
            (f" Working (Live): {live}\n", None),
            ("🔴", "red"),
            (f" Dead: {dead}\n", None),
            ("🔄", "recycle"),
            (f" Recovered via US fallback: {fallback}\n", None),
            ("📈", "chart"),
            (f" Success Rate: {success_rate:.1f}%", None),
        ]
    )
    return text, entities


# ---------------------------------------------------------------------------
# Proxy parsing
# ---------------------------------------------------------------------------


def parse_proxy_line(line: str):
    """Returns (proxy_url, original_line) or None."""
    line = line.strip()
    if not line:
        return None

    if "@" in line:
        try:
            creds, hostport = line.split("@", 1)
            user, password = creds.split(":", 1)
            host, port = hostport.split(":", 1)
            return f"http://{user}:{password}@{host}:{port}", line
        except Exception:
            return None

    parts = line.split(":")
    if len(parts) == 4:
        ip, port, user, password = parts
        return f"http://{user}:{password}@{ip}:{port}", line
    elif len(parts) == 2:
        ip, port = parts
        return f"http://{ip}:{port}", line
    return None


# ---------------------------------------------------------------------------
# Proxy liveness check (with a secondary/US fallback endpoint)
# ---------------------------------------------------------------------------


async def check_proxy(session: aiohttp.ClientSession, proxy_url: str, sem: asyncio.Semaphore):
    """Returns 'live', 'fallback', or 'dead'."""
    timeout = aiohttp.ClientTimeout(total=8)
    async with sem:
        try:
            async with session.get(TEST_URL_PRIMARY, proxy=proxy_url, timeout=timeout) as resp:
                if resp.status in (200, 204):
                    return "live"
        except Exception:
            pass

        try:
            async with session.get(TEST_URL_FALLBACK_US, proxy=proxy_url, timeout=timeout) as resp:
                if resp.status in (200, 204):
                    return "fallback"
        except Exception:
            pass

        return "dead"


# ---------------------------------------------------------------------------
# Owl balance lookup (kept from the original bot, exposed via /balance)
# ---------------------------------------------------------------------------


async def get_owl_balance():
    url = "https://api.owlproxy.com/openApi/vcDynamicGood/queryCurrentTrafficBalance"
    headers = {
        "Content-Type": "application/json",
        "accessKeyId": OWL_AK,
        "secretAccessKey": OWL_SK,
    }
    payloads = [
        {"accessKeyId": OWL_AK, "secretAccessKey": OWL_SK},
        {"accessKey": OWL_AK, "secretKey": OWL_SK},
        {"ak": OWL_AK, "sk": OWL_SK},
    ]
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for payload in payloads:
            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    text_resp = await resp.text()
                    try:
                        data_json = json.loads(text_resp)
                        if resp.status == 200 and data_json.get("code") == 200:
                            return data_json.get("data")
                    except Exception:
                        continue
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# Core bulk-check pipeline
# ---------------------------------------------------------------------------


async def process_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE, lines):
    register_user(update.effective_user.id)

    parsed = [r for r in (parse_proxy_line(l) for l in lines) if r]
    if not parsed:
        await update.message.reply_text("❌ কোনো ভ্যালিড প্রক্সি পাওয়া যায়নি। ফরম্যাট চেক করুন।")
        return

    total = len(parsed)
    status_msg = await update.message.reply_text(f"🔄 প্রক্সি চেক করা হচ্ছে... (0/{total})")

    sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    lock = asyncio.Lock()
    state = {"done": 0, "live": 0, "dead": 0, "fallback": 0}
    live_lines = []
    last_edit = {"t": 0.0}

    async def worker(session, proxy_url, original_line):
        result = await check_proxy(session, proxy_url, sem)
        async with lock:
            state["done"] += 1
            if result in ("live", "fallback"):
                state["live"] += 1
                live_lines.append(original_line)
                if result == "fallback":
                    state["fallback"] += 1
            else:
                state["dead"] += 1

            now = asyncio.get_event_loop().time()
            if now - last_edit["t"] > 1.2 or state["done"] == total:
                last_edit["t"] = now
                text = build_progress_message(state["done"], total, state["live"], state["dead"])
                try:
                    await status_msg.edit_text(text)
                except Exception:
                    pass

    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(*(worker(session, url, line) for url, line in parsed))

    summary_text, summary_entities = build_summary_message(
        total, state["live"], state["dead"], state["fallback"]
    )
    await status_msg.edit_text(summary_text, entities=summary_entities)

    if not live_lines:
        await update.message.reply_text("😞 কোনো লাইভ প্রক্সি পাওয়া যায়নি।")
        return

    filename = f"live_proxies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = f"/tmp/{filename}"
    with open(filepath, "w") as f:
        f.write("\n".join(live_lines))

    with open(filepath, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=filename,
            caption=f"🟢 Here are your live proxies ({state['live']}/{total})",
        )
    os.remove(filepath)

    header = "📋 Live Proxies List:\n\n"
    chunk = header
    for line in live_lines:
        if len(chunk) + len(line) + 1 > 3900:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await update.message.reply_text(chunk)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user.id)
    await update.message.reply_text(WELCOME_TEXT)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user.id)
    lines = [l for l in update.message.text.split("\n") if l.strip()]
    await process_proxies(update, context, lines)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user.id)
    doc = update.message.document
    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("❌ শুধুমাত্র .txt ফাইল সাপোর্টেড।")
        return
    tg_file = await doc.get_file()
    raw = await tg_file.download_as_bytearray()
    lines = [l for l in raw.decode("utf-8", errors="ignore").split("\n") if l.strip()]
    await process_proxies(update, context, lines)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ আপনি admin না।")
        return
    await update.message.reply_text(
        f"🛠 Admin Panel\n\n"
        f"👥 Total Users: {len(USERS)}\n\n"
        f"📢 Broadcast করতে চাইলে লিখুন:\n/broadcast আপনার মেসেজ"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /broadcast আপনার মেসেজ")
        return
    message = " ".join(context.args)
    sent, failed = 0, 0
    for uid in list(USERS):
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Broadcast সম্পন্ন।\nপাঠানো হয়েছে: {sent}\nব্যর্থ: {failed}")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data = await get_owl_balance()
    if data and isinstance(data, dict):
        rem = data.get("remainingTraffic", "N/A")
        used = data.get("useTraffic", "N/A")
        total = data.get("accumulatedTraffic", "N/A")
        await update.message.reply_text(
            f"📊 Owl Account Balance\n\n"
            f"অবশিষ্ট: {rem} MB\nব্যবহৃত: {used} MB\nমোট প্যাকেজ: {total} MB"
        )
    else:
        await update.message.reply_text("❌ ব্যালেন্স তথ্য আনা যায়নি।")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN সেট করা নেই!")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Bot Started!")
    app.run_polling()


if __name__ == "__main__":
    main()
