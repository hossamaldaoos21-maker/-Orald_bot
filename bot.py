
import logging
import os
import sqlite3

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "consultations.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS message_routes (
                admin_message_id INTEGER PRIMARY KEY,
                patient_chat_id INTEGER NOT NULL
            )
        """)


def remember_message(admin_message_id, patient_chat_id):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """INSERT OR REPLACE INTO message_routes
               (admin_message_id, patient_chat_id)
               VALUES (?, ?)""",
            (admin_message_id, patient_chat_id),
        )


def find_patient(admin_message_id):
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            """SELECT patient_chat_id FROM message_routes
               WHERE admin_message_id = ?""",
            (admin_message_id,),
        ).fetchone()
    return row[0] if row else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    if update.effective_chat.id == ADMIN_CHAT_ID:
        await update.message.reply_text(
            "Orald admin mode is ready.\n\n"
            "To reply to a patient, use Telegram's Reply function "
            "on one of their forwarded messages, then send your text or photo."
        )
        return

    await update.message.reply_text(
        "Welcome to Orald, Dr. Hossam's dental consultation service.\n\n"
        "You can send your dental question as a text message or photo. "
        "You may send follow-up messages at any time.\n\n"
        "A dentist will review your messages and respond when available. "
        "This service does not replace an in-person examination.\n\n"
        "If you have difficulty breathing or swallowing, severe or rapidly "
        "increasing facial swelling, uncontrolled bleeding, or serious injury, "
        "seek urgent medical care immediately.\n\n"
        "Please avoid sending unnecessary personal or identifying information."
    )


async def patient_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat or chat.type != "private":
        return

    if chat.id == ADMIN_CHAT_ID:
        return

    user = update.effective_user
    display_name = user.full_name if user else "Unknown patient"

    try:
        header = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                "PATIENT MESSAGE\n"
                f"Name: {display_name}\n"
                f"Patient Telegram ID: {chat.id}\n"
                "Reply directly to this notice or the copied message below."
            ),
        )
        remember_message(header.message_id, chat.id)

        copied = await context.bot.copy_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
        remember_message(copied.message_id, chat.id)

        await message.reply_text(
            "Your message has been sent to the dentist. "
            "You can send more messages or photos in this chat."
        )

    except Exception:
        logger.exception("Could not forward patient message")
        await message.reply_text(
            "Sorry, your message could not be delivered. Please try again later."
        )


async def admin_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat or chat.id != ADMIN_CHAT_ID:
        return

    if not message.reply_to_message:
        await message.reply_text(
            "To contact a patient, use Reply on that patient's forwarded "
            "message or patient notice, then send your text or photo."
        )
        return

    patient_chat_id = find_patient(message.reply_to_message.message_id)

    if not patient_chat_id:
        await message.reply_text(
            "I couldn't identify the patient for that message. "
            "Please reply directly to a forwarded patient message or its notice."
        )
        return

    try:
        await context.bot.copy_message(
            chat_id=patient_chat_id,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
        await message.reply_text("Your reply was sent to the patient.")
    except Exception:
        logger.exception("Could not deliver dentist reply")
        await message.reply_text(
            "The reply could not be delivered. The patient may have blocked the bot."
        )


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "Patients: send your dental question or photo here.\n"
        "Dentist: reply to a forwarded patient message to respond to that patient."
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled bot error: %s", context.error)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN environment variable is not set.")
    if not ADMIN_CHAT_ID:
        raise SystemExit("ADMIN_CHAT_ID environment variable is not set.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & ~filters.COMMAND
            & filters.ALL,
            patient_message,
        ),
        group=0,
    )

    app.add_handler(
        MessageHandler(
            filters.Chat(ADMIN_CHAT_ID)
            & ~filters.COMMAND
            & filters.ALL,
            admin_message,
        ),
        group=1,
    )

    app.add_error_handler(error_handler)

    logger.info("Orald messaging bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
