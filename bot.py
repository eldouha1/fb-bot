import os
import re
import json
import time
import asyncio
import logging
import requests
import subprocess
import threading
from urllib.parse import urlparse
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] %(message)s")
logger = logging.getLogger("MultiPlatformFactChecker")

# خادم Flask لإبقاء الخدمة حية على Render
app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Universal Video Fact-Checker Engine is Live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# رفع حد الرموز لمنع اقتطاع التقرير الأكاديمي
GENERATION_CONFIG = {
    "temperature": 0.2,
    "max_output_tokens": 2500,  # مساحة كافية لتوليد تقارير عربية مفصلة دون توقف مفاجئ
}

model = genai.GenerativeModel(
    model_name="gemini-3.8-flash",
    generation_config=GENERATION_CONFIG
)

PROMPT_TEMPLATE = """
أنت خبير وأكاديمي علمي متخصص في تدقيق المحتوى وفلسفة العلوم. قم بتحليل وتفكيك المحتوى المرئي/المستخرج التالي بشكل منهجي ومكتمل دون بتر:
1. **الادعاءات الرئيسية:** استخرج الفرضيات أو المزاعم الأساسية المطروحة.
2. **التقييم العلمي:** (صحيح، مضلل، غير دقيق، مغالطة، أو خارج حدود البحث التجريبي) مع التعليل العلمي الدقيق.
3. **التوصيف العلمي المعتمد:** اذكر الحقائق العلمية الموثقة والنماذج المعتمدة (تطورياً، جينياً، فيزيائياً، أو إبستمولوجياً) بتفصيل دقيق يوضح سياق الظاهرة بالكامل.
"""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def resolve_url(url: str) -> str:
    try:
        res = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=8)
        return res.url
    except Exception:
        return url

# استخراج مسار Meta CDN المباشر لفيسبوك
def find_facebook_cdn_url(html: str) -> str:
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
                clean = json.loads(f'"{clean}"')
            except Exception:
                pass
            if "fbcdn.net" in clean or "http" in clean:
                return clean
    return None

# محرك السحب الذكي الشامل (Facebook, TikTok, YouTube, Instagram)
def universal_video_downloader(url: str, output_path: str):
    real_url = resolve_url(url)
    domain = urlparse(real_url).netloc.lower()
    logger.info(f"المنصة المستهدفة: {domain} | الرابط: {real_url}")

    # 1. إذا كان الرابط من فيسبوك: تجربة الـ CDN السريع أولاً
    if "facebook.com" in domain or "fb.watch" in domain:
        try:
            req = requests.get(real_url, headers=HEADERS, timeout=10)
            cdn_link = find_facebook_cdn_url(req.text)
            if cdn_link:
                with requests.get(cdn_link, headers=HEADERS, stream=True, timeout=25) as r:
                    r.raise_for_status()
                    with open(output_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 20000:
                    return True, "Facebook Direct CDN"
        except Exception as e:
            logger.warning(f"تعذر استخراج CDN فيسبوك: {str(e)}")

    # 2. سحب موحد عبر yt-dlp المحسّن (ليوتيوب وتيك توك والخطة البديلة لفيسبوك)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--socket-timeout", "15",
        "--max-filesize", "45M",
        # اختيار أفضل جودة بصيغة mp4 لا تتجاوز 720p لتسريع المعالجة وحفظ الذاكرة
        "-f", "b[ext=mp4][height<=720]/b[height<=720]/b[ext=mp4]/b",
        "--output", output_path,
        real_url
    ]
    
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 20000:
        if "tiktok.com" in domain:
            return True, "TikTok Stream"
        elif "youtu" in domain:
            return True, "YouTube Engine"
        return True, "Universal yt-dlp"

    return False, "فشل الاستخراج من جميع المسارات"

# إرسال التقارير الطويلة بأمان دون الوقوع في حد 4096 حرفاً لتيليجرام
async def send_full_report(status_msg, update: Update, full_text: str):
    max_len = 4000
    if len(full_text) <= max_len:
        await status_msg.edit_text(full_text)
    else:
        # إرسال الجزء الأول في نفس رسالة الحالة
        await status_msg.edit_text(full_text[:max_len])
        # إرسال الأجزاء المتبقية في رسائل منفصلة متتالية
        remaining = full_text[max_len:]
        while remaining:
            part = remaining[:max_len]
            await update.message.reply_text(part)
            remaining = remaining[max_len:]

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    total_start = time.time()
    
    status_msg = await update.message.reply_text("🚀 [1/4] جاري فك ارتباط المنصة وسحب الفيديو المباشر...")
    temp_video = f"stream_{chat_id}_{int(time.time())}.mp4"
    gemini_file = None

    try:
        success, method = await asyncio.to_thread(universal_video_downloader, url, temp_video)
        
        if not success or not os.path.exists(temp_video):
            await status_msg.edit_text("❌ تعذر تنزيل الفيديو من الرابط. قد يكون الحساب خاصاً أو الفيديو مقيداً.")
            return

        dl_time = round(time.time() - total_start, 2)
        await status_msg.edit_text(f"⚡ [2/4] تم سحب الفيديو بنجاح عبر ({method}) خلال `{dl_time}s`! جاري الرفع لجيميني...")
        
        # رفع الفيديو
        t1 = time.time()
        gemini_file = await asyncio.to_thread(genai.upload_file, temp_video)
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = await asyncio.to_thread(genai.get_file, gemini_file.name)

        up_time = round(time.time() - t1, 2)
        await status_msg.edit_text("🧠 [3/4] جاري تحليل المحتوى البصري والمسار الصوتي ومطابقة الأدلة العلمية...")
        
        # التوليد الفعلي
        t2 = time.time()
        response = await asyncio.to_thread(model.generate_content, [gemini_file, PROMPT_TEMPLATE])
        ai_time = round(time.time() - t2, 2)
        
        total_time = round(time.time() - total_start, 2)
        telemetry = (
            f"\n\n---\n"
            f"📊 **مؤشرات الأداء المنظومي:**\n"
            f"🎯 المنصة والمسار: `{method}`\n"
            f"⚡ السحب: `{dl_time}s` | ☁️ الرفع: `{up_time}s` | 🧠 التحليل: `{ai_time}s` | ⏱️ الإجمالي: `{total_time}s`"
        )
        
        full_report = f"🔬 **التقرير العلمي الشامل لمحتوى الفيديو:**\n\n{response.text}{telemetry}"
        await send_full_report(status_msg, update, full_report)

    except Exception as e:
        logger.error(f"خطأ أثناء المعالجة: {str(e)}")
        await status_msg.edit_text(f"❌ حدث خطأ غير متوقع أثناء الفحص: {str(e)}")
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
