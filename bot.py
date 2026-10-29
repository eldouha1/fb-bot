import os
import asyncio
import subprocess
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai

# خادم ويب لإبقاء الخدمة نشطة 24/7 على Render
app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Scientific Video Fact-Checker is Active!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.8-flash")

PROMPT_TEMPLATE = """
أنت خبير وأكاديمي متخصص في التدقيق العلمي والمنهجي:
1. استخرج الادعاءات أو الفرضيات الأساسية المطروحة في هذا المحتوى بدقة.
2. قيّم مدى صحتها ومطابقتها للحقائق والأدلة العلمية المعتمدة (صحيح، مضلل، غير مثبت، مغالطة).
3. قدّم التفسير العلمي الموثق والبديل الصحيح باختصار منهجي واضح ومباشر.
"""

# استخراج بيانات الرابط في خيط منفصل لتفادي تجميد البوت
def fetch_link_metadata(url: str):
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--socket-timeout", "5",
        "--print", "%(title)s\n\n%(description)s",
        url
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=12)

# تحليل المحتوى بواسطة جيميني في خيط منفصل
def analyze_with_gemini(contents):
    return model.generate_content(contents)

# معالجة الروابط والنصوص
async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if "http://" in text or "https://" in text:
        status_msg = await update.message.reply_text("⏳ جاري فحص الرابط ومحاولة استخراج المحتوى...")
        try:
            # تشغيل الجلب في Thread مستقل لمنع تجميد حلقة الأحداث
            result = await asyncio.to_thread(fetch_link_metadata, text)
            
            if result.returncode != 0 or not result.stdout.strip():
                await status_msg.edit_text(
                    "⚠️ **حظر من المصدر (Facebook Cloud Block):**\n"
                    "تمنع منصة فيسبوك خوادم السحابة من الوصول المباشر للروابط.\n\n"
                    "🎯 **لفحص هذا الفيديو بأداتك الآن:**\n"
                    "قم بتحميل مقطع الفيديو على هاتفك وأرسله مباشرة إلى البوت كملف فيديو، وسيقوم النموذج بفحصه وتدقيقه صوتياً وبصرياً بالكامل."
                )
                return

            await status_msg.edit_text("🔬 تم سحب البيانات، جاري التحليل والتدقيق العلمي...")
            full_prompt = f"{PROMPT_TEMPLATE}\n\nالمحتوى المستخرج:\n{result.stdout.strip()}"
            response = await asyncio.to_thread(analyze_with_gemini, full_prompt)
            
            output = f"🔬 **التقرير والتدقيق العلمي:**\n\n{response.text}"
            await status_msg.edit_text(output[:4000])

        except subprocess.TimeoutExpired:
            await status_msg.edit_text(
                "⏱️ **انتهت مهلة الاتصال بالرابط:**\n"
                "خادم فيسبوك لم يستجب. أرسل ملف الفيديو مباشرة ليتم تحليله داخلياً."
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ حدث خطأ: {str(e)}")
    else:
        # فحص نص أو ادعاء مكتوب
        status_msg = await update.message.reply_text("🔬 جاري التدقيق العلمي للنص...")
        try:
            full_prompt = f"{PROMPT_TEMPLATE}\n\nالادعاء:\n{text}"
            response = await asyncio.to_thread(analyze_with_gemini, full_prompt)
            output = f"🔬 **التقرير العلمي:**\n\n{response.text}"
            await status_msg.edit_text(output[:4000])
        except Exception as e:
            await status_msg.edit_text(f"❌ خطأ أثناء التحليل: {str(e)}")

# معالجة ملفات الفيديو المباشرة (صوت وصورة)
async def handle_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    status_msg = await update.message.reply_text("📥 تم استلام الفيديو، جاري رفعه لمحرك Gemini للتحليل البصري والصوتي...")
    video_path = f"temp_{update.message.chat_id}.mp4"
    
    try:
        file = await context.bot.get_file(video.file_id)
        await file.download_to_drive(video_path)
        
        # رفع الفيديو لـ Gemini File API
        gemini_file = await asyncio.to_thread(genai.upload_file, video_path)
        
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = await asyncio.to_thread(genai.get_file, gemini_file.name)

        if gemini_file.state.name == "FAILED":
            raise ValueError("فشلت خوادم الذكاء الاصطناعي في معالجة إطارات الفيديو.")

        response = await asyncio.to_thread(analyze_with_gemini, [gemini_file, PROMPT_TEMPLATE])
        
        output = f"🔬 **التقرير العلمي الشامل لمحتوى الفيديو:**\n\n{response.text}"
        await status_msg.edit_text(output[:4000])
        
        await asyncio.to_thread(genai.delete_file, gemini_file.name)

    except Exception as e:
        await status_msg.edit_text(f"❌ خطأ أثناء معالجة الفيديو: {str(e)}")
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)

def main():
    threading.Thread(target=run_web, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_or_link))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.ANIMATION, handle_video_file))
    
    app.run_polling()

if __name__ == "__main__":
    main()
