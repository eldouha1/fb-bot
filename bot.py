import os
import time
import asyncio
import logging
import requests
import subprocess
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai

# إعداد السجلات
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] %(message)s")
logger = logging.getLogger("VideoAnalyzer")

app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Video Extraction Engine with Cookies is Active!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-3.8-flash",
    generation_config={"temperature": 0.2, "max_output_tokens": 1000}
)

PROMPT_TEMPLATE = """
أنت خبير وأكاديمي علمي متخصص في تدقيق المحتوى وفلسفة العلوم. قم بتحليل وتفكيك المحتوى المرئي/المستخرج التالي:
1. **الادعاءات الرئيسية:** استخرج الفرضية الأساسية الواردة.
2. **التقييم العلمي:** (صحيح، مضلل، غير دقيق، مغالطة، أو خارج حدود البحث التجريبي) مع تعليل دقيق وموجز.
3. **التوصيف العلمي المعتمد:** اذكر الحقيقة العلمية الموثقة بدقة ومنهجية واضحة.
"""

# البحث عن ملف الكوكيز في مسارات Render أو المجلد المحلي
def get_cookies_path():
    possible_paths = [
        "/etc/secrets/cookies.txt",  # مسار Render Secret Files
        "cookies.txt"                # مسار المجلد المحلي/المستودع
    ]
    for path in possible_paths:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            logger.info(f"🔑 تم العثور على ملف الكوكيز واستخدامه: {path}")
            return path
    logger.warning("⚠️ لم يتم العثور على ملف cookies.txt، المحاولة ستتم بدون توثيق!")
    return None

# فك الروابط المختصرة وروابط المشاركة (Facebook Share URL Normalization)
def resolve_share_url(url: str) -> str:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.head(url, headers=headers, allow_redirects=True, timeout=5)
        return res.url
    except Exception:
        return url

# دالة السحب الذكية باستخدام الكوكيز
def extract_video_stream(url: str, output_path: str):
    real_url = resolve_share_url(url)
    cookie_file = get_cookies_path()
    
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--socket-timeout", "10",
        "--max-filesize", "45M",
        "-f", "b[ext=mp4]/b",
        "--output", output_path
    ]
    
    # تفعيل حيلة الكوكيز إذا توفر الملف
    if cookie_file:
        cmd.extend(["--cookies", cookie_file])
        
    cmd.append(real_url)
    
    logger.info(f"بدء سحب الفيديو عبر الأداة للرابط: {real_url}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    
    if result.returncode == 0 and os.path.exists(output_path):
        return True, "نجح التنزيل"
    
    return False, result.stderr

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    total_start = time.time()
    
    status_msg = await update.message.reply_text("🕵️‍♂️ [1/4] جاري اختراق جدار الحماية وسحب الفيديو عبر جلسة الكوكيز الموثقة...")
    temp_video = f"stream_{chat_id}_{int(time.time())}.mp4"
    gemini_file = None

    try:
        # محاولة سحب الفيديو الحقيقي
        success, err_msg = await asyncio.to_thread(extract_video_stream, url, temp_video)
        
        if not success or not os.path.exists(temp_video):
            logger.error(f"فشل سحب الفيديو: {err_msg[:250]}")
            await status_msg.edit_text("❌ لم نتمكن من تنزيل ملف الفيديو. تأكد من صحة وتحديث ملف الكوكيز.")
            return

        dl_time = round(time.time() - total_start, 2)
        await status_msg.edit_text(f"🔄 [2/4] تم سحب الفيديو بالكامل ({dl_time}s)! جاري رفعه لمحرك الذكاء الاصطناعي...")
        
        # رفع الفيديو إلى Gemini
        t1 = time.time()
        gemini_file = await asyncio.to_thread(genai.upload_file, temp_video)
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = await asyncio.to_thread(genai.get_file, gemini_file.name)

        up_time = round(time.time() - t1, 2)
        await status_msg.edit_text("🧠 [3/4] جاري تشغيل النموذج لتحليل الصوت والصورة وتفكيك الادعاءات...")
        
        # التحليل بواسطة Gemini
        t2 = time.time()
        response = await asyncio.to_thread(model.generate_content, [gemini_file, PROMPT_TEMPLATE])
        ai_time = round(time.time() - t2, 2)
        
        total_time = round(time.time() - total_start, 2)
        telemetry = (
            f"\n\n---\n"
            f"📊 **مؤشرات الأداء للنظام:**\n"
            f"⚡ سحب الفيديو: `{dl_time}s` | ☁️ الرفع: `{up_time}s` | 🧠 التحليل الأكاديمي: `{ai_time}s` | ⏱️ الإجمالي: `{total_time}s`"
        )
        
        await status_msg.edit_text(f"🔬 **التقرير العلمي الشامل لمحتوى الفيديو:**\n\n{response.text}{telemetry}")

    except Exception as e:
        logger.error(f"خطأ تقني: {str(e)}")
        await status_msg.edit_text(f"❌ حدث خطأ غير متوقع: {str(e)}")
    finally:
        if os.path.exists(temp_video):
            os.remove(temp_video)
        if gemini_file:
            try:
                await asyncio.to_thread(genai.delete_file, gemini_file.name)
            except Exception:
                pass

def main():
    threading.Thread(target=run_web, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    app.run_polling()

if __name__ == "__main__":
    main()
