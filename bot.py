import os
import subprocess
import threading
import time
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai

# خادم ويب لإبقاء الخدمة نشطة 24/7 على Render
app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Scientific Fact-Checker Bot is Running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.8-flash")

PROMPT_TEMPLATE = """
أنت خبير وأكاديمي متخصص في التدقيق العلمي للادعاءات والمعلومات المنشورة على وسائل التواصل الاجتماعي:
1. استخرج الادعاءات الرئيسية الواردة في هذا المحتوى.
2. قيّم صحة ودقة هذه الادعاءات علمياً وأكاديمياً (صحيح، مضلل، غير دقيق، مغالطة).
3. قدّم التفسير العلمي الموثق والبديل الصحيح باختصار ودقة.
"""

# معالجة الروابط النصية
async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # فحص ما إذا كانت الرسالة تحتوي على رابط
    if "http://" in text or "https://" in text:
        status_msg = await update.message.reply_text("⏳ جاري محاولة سحب بيانات الفيديو من الرابط...")
        try:
            cmd = [
                "yt-dlp",
                "--no-playlist",
                "--print", "%(title)s\n\n%(description)s",
                text
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            
            if result.returncode != 0:
                # إذا حظر فيسبوك عنوان السيرفر السحابي
                await status_msg.edit_text(
                    "⚠️ **تعذر استخراج بيانات الفيديو من الرابط:**\n"
                    "تفرض خوارزميات فيسبوك جدار حماية ضد الخوادم السحابية.\n\n"
                    "💡 **لتدقيق هذا الفيديو بأداتك:** أرسل ملف الفيديو أو قم بإعادة توجيهه (Forward) إلى البوت مباشرة ليحلله محرك Gemini صوتاً وصورة."
                )
                return

            extracted_content = result.stdout.strip()
            if not extracted_content:
                await status_msg.edit_text("❌ لم يتم العثور على عنوان أو وصف متاح في هذا الرابط.")
                return

            await status_msg.edit_text("🔬 جاري فحص المحتوى المستخرج وتدقيقه علمياً...")
            full_prompt = f"{PROMPT_TEMPLATE}\n\nالمحتوى المستخرج من الفيديو:\n{extracted_content}"
            response = model.generate_content(full_prompt)
            await status_msg.edit_text(f"🔬 **التقرير العلمي:**\n\n{response.text}")

        except subprocess.TimeoutExpired:
            await status_msg.edit_text("⏱️ انتهت مهلة محاولة الاتصال بالرابط.")
        except Exception as e:
            await status_msg.edit_text(f"❌ خطأ تقني: {str(e)}")
    else:
        # تحليل نص أو ادعاء مكتوب مباشرة
        status_msg = await update.message.reply_text("🔬 جاري فحص وتدقيق النص علمياً...")
        try:
            full_prompt = f"{PROMPT_TEMPLATE}\n\nالنص المرسل:\n{text}"
            response = model.generate_content(full_prompt)
            await status_msg.edit_text(f"🔬 **التقرير العلمي:**\n\n{response.text}")
        except Exception as e:
            await status_msg.edit_text(f"❌ خطأ أثناء التحليل: {str(e)}")

# معالجة ملفات الفيديو المباشرة
async def handle_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    status_msg = await update.message.reply_text("📥 تم استلام الفيديو، جاري نقله وتحليله عبر محرك Gemini...")
    video_path = f"temp_{update.message.chat_id}.mp4"
    
    try:
        file = await context.bot.get_file(video.file_id)
        await file.download_to_drive(video_path)
        
        # رفع الفيديو لواجهة Gemini المتعددة الوسائط
        gemini_file = genai.upload_file(video_path)
        while gemini_file.state.name == "PROCESSING":
            time.sleep(2)
            gemini_file = genai.get_file(gemini_file.name)

        if gemini_file.state.name == "FAILED":
            raise ValueError("فشلت معالجة ملف الفيديو على خوادم الذكاء الاصطناعي.")

        response = model.generate_content([gemini_file, PROMPT_TEMPLATE])
        await status_msg.edit_text(f"🔬 **التقرير العلمي الشامل للفيديو:**\n\n{response.text}")
        genai.delete_file(gemini_file.name)

    except Exception as e:
        await status_msg.edit_text(f"❌ حدث خطأ أثناء معالجة الفيديو: {str(e)}")
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)

def main():
    threading.Thread(target=run_web).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # دعم النصوص والروابط
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_or_link))
    # دعم الفيديوهات المباشرة
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.ANIMATION, handle_video_file))
    
    app.run_polling()

if __name__ == "__main__":
    main()
