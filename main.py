import os
import aiohttp
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

app_web = Flask('')
@app_web.route('/')
def home():
    return "Bot Alive!"

def run_web():
    app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

Thread(target=run_web).start()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# OwlProxy API Keys
OWL_AK = os.environ.get("OWL_ACCESS_KEY", "RMwMg5ff4GcBondlIV71XrNIlUHwRME2")
OWL_SK = os.environ.get("OWL_SECRET_KEY", "4QCN0Ln9014DliUp4n8PXECq")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Proxy MB Checker Bot Active!**\nপ্রক্সি সেন্ড করুন MB চেক করার জন্য।", parse_mode="Markdown")

async def get_owl_balance():
    ak = os.environ.get("OWL_ACCESS_KEY", OWL_AK)
    sk = os.environ.get("OWL_SECRET_KEY", OWL_SK)
    
    url = f"https://api.owlproxy.com/openApi/vcDynamicGood/queryCurrentTrafficBalance?accessKeyId={ak}&secretAccessKey={sk}"
    headers = {
        "accessKeyId": ak,
        "secretAccessKey": sk,
        "Content-Type": "application/json"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    if res.get("code") == 200 and "data" in res:
                        return res["data"]
    except Exception:
        pass
    return None

async def check_proxy(proxy_input: str):
    parts = proxy_input.strip().split(":")
    if len(parts) == 4:
        ip, port, user, password = parts
        proxy_url = f"http://{user}:{password}@{ip}:{port}"
    elif len(parts) == 2:
        user, password = parts
        proxy_url = f"http://{user}:{password}@gate.owlproxy.com:8000"
    else:
        return "❌ **ভুল ফরম্যাট!**"

    target_url = "http://proxy.owlproxy.com/proxy/extract"
    is_active = False

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(target_url, proxy=proxy_url) as response:
                if response.status == 200:
                    is_active = True
    except Exception:
        is_active = False

    if not is_active:
        return "❌ **Proxy Expired / Dead!**"

    balance_data = await get_owl_balance()
    
    reply_msg = "✅ **Proxy Active!**"
    if balance_data:
        rem = balance_data.get("remainingTraffic", "N/A")
        used = balance_data.get("useTraffic", "N/A")
        total = balance_data.get("accumulatedTraffic", "N/A")
        reply_msg += f"\n📊 **অবশিষ্ট MB:** {rem} MB\n📉 **ব্যবহৃত MB:** {used} MB\n📦 **মোট প্যাকেজ:** {total} MB"
    else:
        reply_msg += "\n📊 **অবশিষ্ট MB:** সচল (Data Active)"

    return reply_msg

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip().split("\n")[0]
    msg = await update.message.reply_text("🔄 প্রক্সি চেক করা হচ্ছে...")
    result = await check_proxy(user_text)
    await msg.edit_text(result, parse_mode="Markdown")

def main():
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN সেট করা নেই!")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    main()
