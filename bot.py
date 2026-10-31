import os
import re
import json
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

# إعداد التسجيل والمراقبة
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] %(message)s")
logger = logging.getLogger("CDNExtractor")

app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Direct CDN Video Analyzer Engine is Live!"

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
1. **الادعاءات الرئيسية:** استخرج الفرضية الأساسية الواردة بدقة.
2. **التقييم العلمي:** (صحيح، مضلل، غير دقيق، مغالطة، أو خارج حدود البحث التجريبي) مع تعليل دقيق وموجز.
3. **التوصيف العلمي المعتمد:** اذكر الحقيقة العلمية الموثقة بدقة ومنهجية واضحة.
"""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Mode": "navigate"
}

# 1. فك روابط المشاركة للحصول على الرابط الحقيقي
def resolve_url(url: str) -> str:
    try:
        res = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=8)
        return res.url
    except Exception:
        return url

# 2. استخراج رابط CDN المباشر من كود الصفحة
def find_direct_cdn_url(html: str) -> str:
    patterns = [
        r'"browser_native_hd_url":"([^"]+)"',
        r'"browser_native_sd_url":"([^"]+)"',
        r'"playable_url_quality_hd":"([^"]+)"',
        r'"playable_url":"([^"]+)"',
        r'playable_url_quality_hd:\\"([^\\"]+)\\"',
        r'playable_url:\\"([^\\"]+)\\"',
        r'hd_src:"([^"]+)"',
        r'sd_src:"([^"]+)"'
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, html)
        for match in matches:
            clean = match.replace(r'\/', '/').replace('\\', '')
            try:
                # فك ترميز JSON/Unicode
                clean = json.loads(f'"{clean}"')
            except Exception:
                pass
            if "fbcdn.net" in clean or "http" in clean:
                return clean
    return None

# 3. سحب الفيديو عبر مسار CDN المباشر مع Fallback لـ yt-dlp
def extract_video(url: str, output_path: str):
    real_url = resolve_url(url)
    logger.info(f"الرابط المباشر بعد التحويل: {real_url}")
    
    try:
        # أ) جلب HTML ومحاولة صيد رابط الـ CDN
        req = requests.get(real_url, headers=HEADERS, timeout=10)
        cdn_link = find_direct_cdn_url(req.text)
        
        if cdn_link:
            logger.info("تم العثور على رابط Meta CDN المباشر بنجاح! جاري التنزيل...")
            with requests.get(cdn_link, headers=HEADERS, stream=True, timeout=25) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 20000:
                return True, "Meta CDN Direct"
    except Exception as e:
        logger.warning(f"تعذر استخراج CDN المباشر، الانتقال للخطة البديلة: {str(e)}")

    # ب) الخطة البديلة: محاولة التنزيل عبر yt-dlp السريع
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--socket-timeout", "10",
        "--max-filesize", "45M",
        "-f", "b[ext=mp4]/b",
        "--output", output_path,
        real_url
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    if res.returncode == 0 and os.path.exists(output_path):
        return True, "yt-dlp"

    return False, "فشل السحب المباشر"

# معالج الرسائل
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    total_start = time.time()
    
    status_msg = await update.message.reply_text("🚀 [1/4] جاري فك مسار الفيديو واصطياد رابط الـ CDN المباشر...")
    temp_video = f"stream_{chat_id}_{int(time.time())}.mp4"
    gemini_file = None

    try:
        success, method = await asyncio.to_thread(extract_video, url, temp_video)
        
        if not success or not os.path.exists(temp_video):
            await status_msg.edit_text("❌ لم يتم العثور على تدفق الفيديو المفتوح في هذا الرابط.")
            return

        dl_time = round(time.time() - total_start, 2)
        await status_msg.edit_text(f"⚡ [2/4] تم سحب الفيديو عبر ({method}) في `{dl_time}s`! جاري رفعه لمحرك الذكاء الاصطناعي...")
        
        # رفع الفيديو لواجهة Gemini
        t1 = time.time()
        gemini_file = await asyncio.to_thread(genai.upload_file, temp_video)
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = await asyncio.to_thread(genai.get_file, gemini_file.name)

        up_time = round(time.time() - t1, 2)
        await status_msg.edit_text("🧠 [3/4] جاري تحليل المحتوى البصري والصوتي علمياً...")
        
        # التحليل بواسطة Gemini
        t2 = time.time()
        response = await asyncio.to_thread(model.generate_content, [gemini_file, PROMPT_TEMPLATE])
        ai_time = round(time.time() - t2, 2)
        
        total_time = round(time.time() - total_start, 2)
        telemetry = (
            f"\n\n---\n"
            f"📊 **مؤشرات الأداء:**\n"
            f"🎯 الطريقة: `{method}` | ⚡ السحب: `{dl_time}s` | ☁️ الرفع: `{up_time}s` | 🧠 التحليل: `{ai_time}s` | ⏱️ الإجمالي: `{total_time}s`"
        )
        
        await status_msg.edit_text(f"🔬 **التقرير العلمي الشامل لمحتوى الفيديو:**\n\n{response.text}{telemetry}")

    except Exception as e:
        logger.error(f"خطأ أثناء المعالجة: {str(e)}")
        await status_msg.edit_text(f"❌ حدث خطأ: {str(e)}")
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
