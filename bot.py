import os
import subprocess
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai
import threading

# خادم ويب وهمي لإبقاء البوت مستيقظاً على Render
app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

# قراءة التوكن والمفتاح من إعدادات Render السحابية
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "facebook.com" in url or "fb.watch" in url:
        status_msg = await update.message.reply_text("⏳ جاري سحب بيانات الفيديو وتحليله علمياً عبر السحابة...")
        try:
            # سحب محتوى فيسبوك عبر yt-dlp
            cmd = ["yt-dlp", "--print", "%(title)s\n%(description)s", url]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            video_content = result.stdout.strip()

            if not video_content:
                await status_msg.edit_text("❌ تعذر استخراج المحتوى من الرابط.")
                return

            # تحليل المحتوى عبر جيميني
            prompt = f"أنت خبير علمي وأكاديمي. قم بتحليل محتوى الفيديو التالي المستخرج من فيسبوك ووضح بدقة إن كان دقيقاً علمياً أم لا باختصار:\n\n{video_content}"
            response = model.generate_content(prompt)

            await status_msg.edit_text(f"🔬 **التقييم العلمي من جيميني:**\n\n{response.text}")
        except Exception as e:
            await status_msg.edit_text(f"❌ حدث خطأ أثناء المعالجة: {str(e)}")
    else:
        await update.message.reply_text("⚠️ يرجى إرسال رابط فيسبوك صالح.")

def main():
    # تشغيل خادم الويب في الخلفية
    threading.Thread(target=run_web).start()

    # تشغيل بوت تيليجرام
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_link))
    app.run_polling()

if __name__ == "__main__":
    main()
