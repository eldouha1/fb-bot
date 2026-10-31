import os
import time
import asyncio
import logging
import traceback
import subprocess
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai

# إعداد نظام المراقبة وتسجيل الأحداث (Logging)
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - [%(funcName)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("FactCheckerBot")

# خادم Flask لإبقاء الخدمة نشطة 24/7 على منصة Render
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Scientific Fact-Checker Service is Active & Healthy!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

# جلب المفاتيح السرية
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    logger.critical("المفاتيح غير متوفرة في بيئة العمل (Environment Variables)!")

# تهيئة Gemini
genai.configure(api_key=GEMINI_API_KEY)

# إعدادات سرعة الاستجابة والدقة المعرفية
GENERATION_CONFIG = {
    "temperature": 0.2,       # تقليل التشتت والتركيز على الدقة الأكاديمية
    "max_output_tokens": 900, # حصر الرموز لتسريع زمن التوليد
}

model = genai.GenerativeModel(
    model_name="gemini-3.8-flash",
    generation_config=GENERATION_CONFIG
)

PROMPT_TEMPLATE = """
أنت خبير أكاديمي في فلسفة العلم والتدقيق البيولوجي والمعرفي. قم بتقييم المحتوى التالي بدقة وإيجاز:

1. **الادعاء الأساسي:** حدد الفكرة الجوهرية في جملة واحدة.
2. **التقييم العلمي/المعرفي:** (صحيح، غير دقيق، مغالطة، أو خارج حدود التجريب العلمي) مع تعليل مكثف في حدود سطرين إلى ثلاثة.
3. **البديل والتوصيف العلمي المثبت:** اشرح التفسير العلمي المعتمد والموثق (تطورياً/جينياً/إبستمولوجياً) باختصار منهجي واضح.
"""

# استخراج نصوص وبيانات الرابط بشكل سريع ومجرد
def fetch_metadata_fast(url: str):
    start = time.time()
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--no-playlist",
        "--no-check-certificates",
        "--socket-timeout", "5",
        "--no-warnings",
        "--print", "%(title)s\n\n%(description)s",
        url
    ]
    logger.info(f"بدء استخراج البيانات من الرابط: {url}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    elapsed = round(time.time() - start, 2)
    logger.info(f"اكتمل استخراج البيانات خلال {elapsed}s مع ReturnCode: {result.returncode}")
    return result, elapsed

# إرسال المحتوى لـ Gemini في Thread مستقل
def call_gemini(contents):
    start = time.time()
    logger.info("إرسال الطلب إلى واجهة برمجة تطبيقات Gemini...")
    res = model.generate_content(contents)
    elapsed = round(time.time() - start, 2)
    logger.info(f"تم استلام استجابة Gemini بنجاح خلال {elapsed}s")
    return res, elapsed

# 1. معالج النصوص والروابط
async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    chat_id = update.message.chat_id
    total_start = time.time()
    
    # فحص ما إذا كان المدخل رابطاً
    if "http://" in user_input or "https://" in user_input:
        status_msg = await update.message.reply_text("⏳ [مرحلة 1/3] جاري فحص الرابط واستخراج البيانات بسرعة...")
        
        # أ) سحب البيانات من الرابط
        try:
            result, fetch_time = await asyncio.to_thread(fetch_metadata_fast, user_input)
            
            if result.returncode != 0 or not result.stdout.strip():
                logger.warning(f"فشل الرابط أو تم حظره. خطأ النظام: {result.stderr.strip()[:200]}")
                await status_msg.edit_text(
                    "⚠️ **تعذر استخراج بيانات الفيديو:**\n"
                    "خوادم فيسبوك تحظر عناوين السحابة من القراءة المباشرة.\n\n"
                    "💡 **الحل الفعال لمشروعك:** قم بإرسال أو تحويل (Forward) مقطع الفيديو ذاته مباشرة إلى البوت، ليتم فحصه صوتياً وبصرياً بدقة كاملة."
                )
                return

            extracted_text = result.stdout.strip()
            
        except subprocess.TimeoutExpired:
            logger.error("انتهت مهلة استدعاء الرابط (Timeout: 10s)")
            await status_msg.edit_text("⏱️ **الموقع المستهدف لم يستجب في الوقت المحدد (10s).** أرسل مقطع الفيديو مباشرة كملف.")
            return
        except Exception as e:
            logger.error(f"خطأ غير متوقع في مرحلة استخراج الرابط:\n{traceback.format_exc()}")
            await status_msg.edit_text(f"❌ خطأ تقني في معالجة الرابط: {str(e)}")
            return

        # ب) التحليل عبر الذكاء الاصطناعي
        await status_msg.edit_text(f"🔬 [مرحلة 2/3] تم الجلب في ({fetch_time}s)، جاري التدقيق العلمي عبر Gemini...")
        
        try:
            full_prompt = f"{PROMPT_TEMPLATE}\n\nالمحتوى المستخرج:\n{extracted_text}"
            response, ai_time = await asyncio.to_thread(call_gemini, full_prompt)
            
            total_time = round(time.time() - total_start, 2)
            telemetry = (
                f"\n\n---\n"
                f"📊 **مؤشرات الأداء السحابي (Telemetry):**\n"
                f"⏱️ الجلب: `{fetch_time}s` | التحليل: `{ai_time}s` | الإجمالي: `{total_time}s`"
            )
            
            await status_msg.edit_text(f"🔬 **التقرير والتدقيق العلمي:**\n\n{response.text}{telemetry}")
            logger.info(f"تمت معالجة الرابط بنجاح للمستخدم {chat_id} في إجمالي وقت {total_time}s")

        except Exception as e:
            logger.error(f"خطأ أثناء استدعاء Gemini:\n{traceback.format_exc()}")
            await status_msg.edit_text(f"❌ فشل تحليل المحتوى عبر الذكاء الاصطناعي: {str(e)}")

    else:
        # معالجة النصوص المباشرة
        status_msg = await update.message.reply_text("🔬 جاري فحص وتدقيق النص علمياً...")
        try:
            full_prompt = f"{PROMPT_TEMPLATE}\n\nالنص/الادعاء المراد فحصه:\n{user_input}"
            response, ai_time = await asyncio.to_thread(call_gemini, full_prompt)
            total_time = round(time.time() - total_start, 2)
            
            telemetry = f"\n\n---\n⚡ زمن المعالجة: `{total_time}s`"
            await status_msg.edit_text(f"🔬 **التقرير العلمي:**\n\n{response.text}{telemetry}")
        except Exception as e:
            logger.error(f"خطأ في معالجة النص:\n{traceback.format_exc()}")
            await status_msg.edit_text(f"❌ خطأ أثناء التدقيق: {str(e)}")

# 2. معالج ملفات الفيديو المباشرة
async def handle_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    chat_id = update.message.chat_id
    total_start = time.time()
    
    status_msg = await update.message.reply_text("📥 [مرحلة 1/4] جاري تنزيل الفيديو مؤقتاً على السحابة...")
    temp_file = f"temp_{chat_id}_{int(time.time())}.mp4"
    gemini_file = None

    try:
        # تحميل الملف من تيليجرام
        t0 = time.time()
        file_obj = await context.bot.get_file(video.file_id)
        await file_obj.download_to_drive(temp_file)
        dl_time = round(time.time() - t0, 2)
        logger.info(f"تم تنزيل الفيديو محلياً في {dl_time}s")

        # رفع الفيديو إلى Gemini File API
        await status_msg.edit_text(f"🔄 [مرحلة 2/4] جاري رفع الفيديو لمنظومة Gemini (تنزيل: {dl_time}s)...")
        t1 = time.time()
        gemini_file = await asyncio.to_thread(genai.upload_file, temp_file)
        
        # انتظار معالجة إطارات الفيديو من طرف جوجل
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = await asyncio.to_thread(genai.get_file, gemini_file.name)

        if gemini_file.state.name == "FAILED":
            raise ValueError("فشلت خوادم جوجل في معالجة إطارات الفيديو.")

        up_time = round(time.time() - t1, 2)
        logger.info(f"اكتملت معالجة الفيديو في واجهة جوجل خلال {up_time}s")

        # التوليد الفعلي والتحليل
        await status_msg.edit_text("🧠 [مرحلة 3/4] جاري الاستماع وتحليل الصوت والصورة وتفكيك الادعاءات...")
        response, ai_time = await asyncio.to_thread(call_gemini, [gemini_file, PROMPT_TEMPLATE])
        
        total_time = round(time.time() - total_start, 2)
        telemetry = (
            f"\n\n---\n"
            f"📊 **مؤشرات الأداء للفيديو الكامل:**\n"
            f"📥 تنزيل: `{dl_time}s` | ☁️ رفع ومعالجة: `{up_time}s` | 🧠 تحليل: `{ai_time}s` | ⏱️ إجمالي: `{total_time}s`"
        )
        
        await status_msg.edit_text(f"🔬 **التقرير العلمي الشامل لمحتوى الفيديو:**\n\n{response.text}{telemetry}")

    except Exception as e:
        logger.error(f"خطأ في معالجة ملف الفيديو:\n{traceback.format_exc()}")
        await status_msg.edit_text(f"❌ حدث خطأ أثناء فحص الفيديو: {str(e)}")

    finally:
        # تنظيف الملفات فوراً للحفاظ على موارد الخادم
        if os.path.exists(temp_file):
            os.remove(temp_file)
        if gemini_file:
            try:
                await asyncio.to_thread(genai.delete_file, gemini_file.name)
            except Exception:
                pass

def main():
    # تشغيل خادم الويب في الخلفية
    threading.Thread(target=run_web, daemon=True).start()
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # ربط معالجات الروابط والمستندات
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_or_link))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.ANIMATION, handle_video_file))
    
    logger.info("البوت جاهز ويعمل بنظام الاستماع (Polling) المزدوج...")
    app.run_polling()

if __name__ == "__main__":
    main()
