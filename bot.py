import os
import subprocess
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai
import threading

# خادم ويب لإبقاء البوت مستيقظاً على Render
app_web = Flask(__name__)
@app_web.route('/')
def home():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.8-flash")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "facebook.com" in url or "fb.watch" in url:
        status_msg = await update.message.reply_text("⏳ جاري محاولة سحب بيانات الفيديو عبر محاكي السحابة...")
        try:
            # إضافة User-Agent وهمي لهاتف محمول لخداع فلتر فيسبوك
            cmd = [
                "yt-dlp",
                "--user-agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                "--print", "%(title)s\n%(description)s",
                url
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
            video_content = result.stdout.strip()
            
            if not video_content:
                await status_msg.edit_text("❌ تعذر استخراج المحتوى. قد يكون الفيديو خاصاً (Private) أو محظوراً.")
                return

            prompt = f"أنت خبير علمي وأكاديمي. قم بتحليل محتوى الفيديو التالي المستخرج من فيسبوك ووضح بدقة إن كان دقيقاً علمياً أم لا باختصار:\n\n{video_content}"
            
            response = model.generate_content(prompt)
            await status_msg.edit_text(f"🔬 **التقييم العلمي من جيميني:**\n\n{response.text}")
            
        except subprocess.TimeoutExpired:
            await status_msg.edit_text(
                "❌ **عذراً، فيسبوك يحظر خوادم المنصات المجانية (Render).**\n\n"
                "💡 **الحل البديل السريع والفعال:**\n"
                "بدلاً من إرسال رابط الفيديو، قم بنسخ **نص المنشور أو الوصف** من فيسبوك وأرسله مباشرة للبوت، وسيقوم بتحليله علمياً لك فوراً وبدون أي مشاكل!"
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ حدث خطأ أثناء المعالجة: {str(e)}")
    else:
        await update.message.reply_text("⚠️ يرجى إرسال رابط فيسبوك صالح.")

def main():
    threading.Thread(target=run_web).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_link))
    app.run_polling()

if __name__ == "__main__":
    main()
