import os
import time
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai
import threading

# خادم ويب لإبقاء البوت مستيقظاً على Render 24/7..1
app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Bot is running online and ready for video analysis!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.8-flash")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # التأكد من أن الرسالة تحتوي على فيديو أو ملف مرئي
    video = update.message.video or update.message.document
    
    if not video:
        await update.message.reply_text("⚠️ أهلاً بك في أداة فحص الفيديوهات. يرجى إرسال أو تحويل **ملف فيديو** ليتم تحليله علمياً.")
        return

    status_msg = await update.message.reply_text("📥 جاري استقبال الفيديو على السحابة...")
    
    video_path = f"temp_{update.message.chat_id}.mp4"
    try:
        # 1. تحميل الفيديو المرفل من تيليجرام
        file = await context.bot.get_file(video.file_id)
        await file.download_to_drive(video_path)
        
        await status_msg.edit_text("🔄 جاري رفع وتحليل محتوى الفيديو عبر محرك Gemini الذكي...")
        
        # 2. رفع الفيديو إلى واجهة برمجة تطبيقات جيميني (Gemini File API)
        gemini_file = genai.upload_file(video_path)
        
        # الانتظار حتى تصبح ومعالجة الملف جاهزة من قبل جوجل إذا كان طويلاً
        while gemini_file.state.name == "PROCESSING":
            time.sleep(2)
            gemini_file = genai.get_file(gemini_file.name)

        if gemini_file.state.name == "FAILED":
            raise ValueError("فشلت معالجة ملف الفيديو من طرف خوادم جوجل.")

        # 3. إرسال الفيديو والطلب العلمي للنموذج
        prompt = """
        أنت خبير علمي وأكاديمي دقيق. قم بتحليل محتوى هذا الفيديو (الكلام، الادعاءات، أو المشاهد المرئية):
        1. هل ما ورد في الفيديو دقيق علمياً أم زائف/غير دقيق؟
        2. قدم تقريراً علمياً مفصلاً وصحيحاً وموثقاً باختصار ومنهجية واضحة.
        """
        
        response = model.generate_content([gemini_file, prompt])
        
        # 4. إرسال النتيجة للمستخدم
        await status_msg.edit_text(f"🔬 **التقرير العلمي والتدقيق الأكاديمي للفيديو:**\n\n{response.text}")
        
        # حذف الملف من خادم جوجل بعد الانتهاء حفاظاً على المساحة
        genai.delete_file(gemini_file.name)

    except Exception as e:
        await status_msg.edit_text(f"❌ حدث خطأ أثناء معالجة الفيديو: {str(e)}")
    
    finally:
        # تنظيف الملف المحلي من سيرفر Render
        if os.path.exists(video_path):
            os.remove(video_path)

def main():
    threading.Thread(target=run_web).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # استقبال الفيديوهات أو المستندات المرئية فقط
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.ANIMATION, handle_video))
    app.run_polling()

if __name__ == "__main__":
    main()
