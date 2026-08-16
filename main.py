import os
import json
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

OWL_AK = "RMwMg5ff4GcBondlIV71XrNIlUHwRME2"
OWL_SK = "4QCN0Ln9014DliUp4n8PXECq"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Proxy MB Checker Bot Active!**\nপ্রক্সি সেন্ড করুন MB চেক করার জন্য।", parse_mode="Markdown")

async def check_proxy_live(proxy_url: str) -> bool:
    test_urls = [
        "http://cp.cloudflare.com/generate_204",
        "http://proxy.owlproxy.com/proxy/extract"
    ]
    timeout = aiohttp.ClientTimeout(total=7)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for url in test_urls:
            try:
                async with session.get(url, proxy=proxy_url) as resp:
                    if resp.status in [200, 204]:
                        return True
            except Exception:
                continue
    return False

async def get_owl_balance():
    url = "https://api.owlproxy.com/openApi/vcDynamicGood/queryCurrentTrafficBalance"
    payload = {
        "accessKeyId": OWL_AK,
        "secretAccessKey": OWL_SK
    }
    headers = {
        "Content-Type": "application/json",
        "accessKeyId": OWL_AK,
        "secretAccessKey": OWL_SK
    }
    
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                return await resp.json()
    except Exception as e:
        try:
            params = {"accessKeyId": OWL_AK, "secretAccessKey": OWL_SK}
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    return await resp.json()
        except Exception as ex:
            return {"error": str(ex)}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip().split("\n")[0]
    msg = await update.message.reply_text("🔄 প্রক্সি চেক করা হচ্ছে...")

    parts = user_text.strip().split(":")
    if len(parts) == 4:
        ip, port, user, password = parts
        proxy_url = f"http://{user}:{password}@{ip}:{port}"
    elif len(parts) == 2:
        user, password = parts
        proxy_url = f"http://{user}:{password}@gate.owlproxy.com:8000"
    else:
        await msg.edit_text("❌ **ভুল প্রক্সি ফরম্যাট!**\nসঠিক ফরম্যাট: `IP:Port:User:Pass`", parse_mode="Markdown")
        return

    is_alive = await check_proxy_live(proxy_url)
    if not is_alive:
        await msg.edit_text("❌ **Proxy Expired / Dead!**", parse_mode="Markdown")
        return

    res_data = await get_owl_balance()

    output = "✅ **Proxy Active!**\n"
    if isinstance(res_data, dict) and res_data.get("code") == 200 and "data" in res_data:
        data = res_data["data"]
        rem = data.get("remainingTraffic", 0)
        used = data.get("useTraffic", 0)
        total = data.get("accumulatedTraffic", 0)
        output += f"\n📊 **অবশিষ্ট MB:** {rem} MB\n📉 **ব্যবহৃত MB:** {used} MB\n📦 **মোট প্যাকেজ:** {total} MB"
    else:
        raw_msg = json.dumps(res_data, ensure_ascii=False) if isinstance(res_data, dict) else str(res_data)
        output += f"\n⚠️ **OwlProxy Response:**\n`{raw_msg}`"

    await msg.edit_text(output, parse_mode="Markdown")

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
