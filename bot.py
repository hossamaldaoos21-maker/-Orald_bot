"""
Orald_bot - Telegram Dental Consultation Bot (MVP)

A simple, professional consultation intake bot:
- Patients describe their dental problem, answer triage questions, upload photos.
- The dentist (admin) receives a formatted consultation with photos and a Reply button.
- The dentist can reply directly to the patient through the bot.

IMPORTANT: This bot is NOT a diagnostic tool. It collects information so a
licensed dentist can review it manually. A safety disclaimer is shown to every
patient before they start.

Tech notes:
- python-telegram-bot v20+ (async), long polling (no webhooks/infra needed).
- SQLite for minimal persistent storage (consultations + mapping to patients).
- All user-facing text lives in the STRINGS dict so Arabic can be added later.
"""

import logging
import os
import sqlite3
import uuid
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration (environment variables - never hard-code secrets)
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "consultations.db")

MAX_PHOTOS = 5
MIN_PHOTOS = 0  # patients may skip photos if they have none

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User-facing strings (English for now; add e.g. STRINGS_AR later and switch)
# ---------------------------------------------------------------------------
STRINGS = {
    "welcome": (
        "Hello, {name}! Welcome to the dental consultation service of Dr. Hossam.\n\n"
        "This service provides general dental guidance based on the information and "
        "images you provide. It does not replace an in-person dental examination.\n\n"
        "If you have severe facial swelling, difficulty breathing or swallowing, "
        "uncontrolled bleeding, serious trauma, or another emergency, please seek "
        "urgent medical care immediately.\n\n"
        "To begin your consultation, please tell me your full name."
    ),
    "ask_age": "Thank you, {name}. What is your age?",
    "ask_problem": "What is your main dental problem? Please choose one:",
    "ask_symptoms": "Please describe your symptoms in your own words (e.g. what hurts, what makes it worse):",
    "ask_pain": "How severe is your pain?",
    "ask_duration": "How long have you had this problem?",
    "ask_flags": "Are you experiencing any of the following?",
    "ask_photos": (
        "Please upload 1 to 5 clear photos of the affected area (tooth / gums / cheek).\n"
        "You can also press Skip if you have no photos."
    ),
    "photo_saved": "Photo {n} received. Send more or press Done.",
    "review": "Please review your consultation:\n\n{summary}\n\nIs everything correct?",
    "submitted": (
        "Thank you. Your consultation has been received and will be reviewed by the "
        "dentist. You will receive a reply here as soon as possible.\n"
        "Your consultation ID: {cid}"
    ),
    "cancelled": "Consultation cancelled. You can start again anytime with /start.",
    "invalid_age": "Please enter a valid age as a number (e.g. 32).",
    "not_a_photo": "Please send a photo image (or press Skip/Done).",
    "reply_prefix": "Message from your dentist:\n\n",
}

PROBLEM_OPTIONS = [
    "Tooth pain", "Broken / chipped tooth", "Gum problem / bleeding",
    "Swelling", "Wisdom tooth", "Sensitivity", "Cosmetic question", "Other",
]
PAIN_OPTIONS = ["No pain", "Mild", "Moderate", "Severe"]
DURATION_OPTIONS = ["Less than 24 hours", "A few days", "1-2 weeks", "More than 2 weeks"]
FLAG_OPTIONS = ["Facial swelling", "Fever", "Bleeding", "Recent trauma / injury", "None of these"]

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------
(
    NAME,
    AGE,
    PROBLEM,
    SYMPTOMS,
    PAIN,
    DURATION,
    FLAGS,
    PHOTOS,
    REVIEW,
) = range(9)

# In-memory draft consultations (patient chat_id -> dict)
drafts: dict[int, dict] = {}
# Pending dentist reply state: admin chat -> consultation_id waiting for reply text
pending_replies: dict[int, str] = {}

# ---------------------------------------------------------------------------
# Storage (SQLite - minimal, safe)
# ---------------------------------------------------------------------------
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS consultations (
                id TEXT PRIMARY KEY,
                patient_chat_id INTEGER NOT NULL,
                patient_name TEXT NOT NULL,
                age INTEGER,
                problem TEXT,
                symptoms TEXT,
                pain TEXT,
                duration TEXT,
                flags TEXT,
                photo_file_ids TEXT,          -- comma-separated Telegram file_ids
                created_at TEXT NOT NULL
            )
            """
        )


def save_consultation(d: dict) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            INSERT INTO consultations
            (id, patient_chat_id, patient_name, age, problem, symptoms,
             pain, duration, flags, photo_file_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                d["id"], d["chat_id"], d["name"], d["age"], d["problem"],
                d["symptoms"], d["pain"], d["duration"],
                ", ".join(d["flags"]) if d["flags"] else "",
                ",".join(d["photos"]), d["created_at"],
            ),
        )


def get_patient_chat_id(consultation_id: str):
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT patient_chat_id FROM consultations WHERE id = ?",
            (consultation_id,),
        ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------
def _keyboard(options: list[str], per_row: int = 2) -> ReplyKeyboardMarkup:
    rows = [options[i:i + per_row] for i in range(0, len(options), per_row)]
    return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)


# ---------------------------------------------------------------------------
# Patient conversation handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    drafts[chat_id] = {"photos": [], "flags": []}
    await update.message.reply_text(
        STRINGS["welcome"].format(name=update.effective_user.first_name),
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    d = drafts[update.effective_chat.id]
    d["name"] = update.message.text.strip()[:100]
    await update.message.reply_text(STRINGS["ask_age"].format(name=d["name"]))
    return AGE


async def got_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or not (0 < int(text) < 120):
        await update.message.reply_text(STRINGS["invalid_age"])
        return AGE
    drafts[update.effective_chat.id]["age"] = int(text)
    await update.message.reply_text(
        STRINGS["ask_problem"], reply_markup=_keyboard(PROBLEM_OPTIONS)
    )
    return PROBLEM


async def got_problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    drafts[update.effective_chat.id]["problem"] = update.message.text.strip()[:200]
    await update.message.reply_text(
        STRINGS["ask_symptoms"], reply_markup=ReplyKeyboardRemove()
    )
    return SYMPTOMS


async def got_symptoms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    drafts[update.effective_chat.id]["symptoms"] = update.message.text.strip()[:2000]
    await update.message.reply_text(
        STRINGS["ask_pain"], reply_markup=_keyboard(PAIN_OPTIONS)
    )
    return PAIN


async def got_pain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    drafts[update.effective_chat.id]["pain"] = update.message.text.strip()
    await update.message.reply_text(
        STRINGS["ask_duration"], reply_markup=_keyboard(DURATION_OPTIONS)
    )
    return DURATION


async def got_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    drafts[update.effective_chat.id]["duration"] = update.message.text.strip()
    await update.message.reply_text(
        STRINGS["ask_flags"],
        reply_markup=_keyboard(FLAG_OPTIONS),
    )
    return FLAGS


async def got_flags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip()
    d = drafts[update.effective_chat.id]
    if choice in FLAG_OPTIONS and choice != "None of these":
        if choice not in d["flags"]:
            d["flags"].append(choice)
        # allow picking several flags
        keyboard = _keyboard([f for f in FLAG_OPTIONS if f not in d["flags"]] + ["Done"])
        await update.message.reply_text(
            "Noted: " + ", ".join(d["flags"]) + ". Any more? (or press Done)",
            reply_markup=keyboard,
        )
        return FLAGS
    if not d["flags"]:
        d["flags"] = ["None"]
    await update.message.reply_text(
        STRINGS["ask_photos"],
        reply_markup=_keyboard(["Skip"], per_row=1),
    )
    return PHOTOS


def _photos_keyboard() -> ReplyKeyboardMarkup:
    return _keyboard(["Done"], per_row=1)


async def got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    d = drafts[chat_id]

    if update.message.text and update.message.text.strip().lower() in ("skip", "done"):
        return await _show_review(update)

    if not update.message.photo:
        await update.message.reply_text(STRINGS["not_a_photo"], reply_markup=_photos_keyboard())
        return PHOTOS

    if len(d["photos"]) >= MAX_PHOTOS:
        return await _show_review(update)

    # Store the highest-resolution file_id; no files are saved to disk.
    d["photos"].append(update.message.photo[-1].file_id)

    if len(d["photos"]) >= MAX_PHOTOS:
        return await _show_review(update)

    await update.message.reply_text(
        STRINGS["photo_saved"].format(n=len(d["photos"])),
        reply_markup=_photos_keyboard(),
    )
    return PHOTOS


def _summary(d: dict) -> str:
    lines = [
        f"Patient: {d.get('name')}",
        f"Age: {d.get('age')}",
        f"Main complaint: {d.get('problem')}",
        f"Symptoms: {d.get('symptoms')}",
        f"Pain: {d.get('pain')}",
        f"Duration: {d.get('duration')}",
        f"Other: {', '.join(d.get('flags', [])) or 'None'}",
        f"Photos: {len(d.get('photos', []))}",
    ]
    return "\n".join(lines)


async def _show_review(update: Update) -> int:
    d = drafts[update.effective_chat.id]
    await update.message.reply_text(
        STRINGS["review"].format(summary=_summary(d)),
        reply_markup=_keyboard(["Submit", "Cancel"], per_row=2),
    )
    return REVIEW


async def got_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip().lower()
    if choice != "submit":
        return await cancel(update, context)

    chat_id = update.effective_chat.id
    d = drafts[chat_id]
    d["id"] = "C-" + uuid.uuid4().hex[:8].upper()
    d["chat_id"] = chat_id
    d["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    save_consultation(d)

    # Notify patient
    await update.message.reply_text(
        STRINGS["submitted"].format(cid=d["id"]),
        reply_markup=ReplyKeyboardRemove(),
    )

    # Notify dentist/admin
    try:
        header = (
            "NEW DENTAL CONSULTATION\n\n"
            + _summary(d).replace(f"Photos: {len(d['photos'])}", "Photos: see below")
            + f"\n\nConsultation ID: {d['id']}\nDate/time: {d['created_at']}"
        )
        reply_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Reply to patient", callback_data=f"reply:{d['id']}")
        ]])
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=header, reply_markup=reply_kb)
        for file_id in d["photos"]:
            await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=file_id)
    except Exception:
        logger.exception("Failed to notify admin about consultation %s", d["id"])

    drafts.pop(chat_id, None)
    logger.info("Consultation %s submitted by chat %s", d["id"], chat_id)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    drafts.pop(update.effective_chat.id, None)
    await update.message.reply_text(
        STRINGS["cancelled"], reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Dentist/admin handlers
# ---------------------------------------------------------------------------
async def admin_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin taps 'Reply to patient' on a consultation card."""
    query = update.callback_query
    await query.answer()
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    consultation_id = query.data.split(":", 1)[1]
    pending_replies[ADMIN_CHAT_ID] = consultation_id
    await query.message.reply_text(
        f"Replying to consultation {consultation_id}. "
        "Please type your message now (send /stopreply to abort)."
    )


async def admin_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin's next message after pressing the Reply button is sent to the patient."""
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_CHAT_ID or chat_id not in pending_replies:
        return
    if not update.message.text:
        return

    consultation_id = pending_replies.pop(chat_id)
    patient_chat_id = get_patient_chat_id(consultation_id)
    if not patient_chat_id:
        await update.message.reply_text(f"Could not find consultation {consultation_id}.")
        return
    try:
        await context.bot.send_message(
            chat_id=patient_chat_id,
            text=STRINGS["reply_prefix"] + update.message.text,
        )
        await update.message.reply_text("Reply sent to the patient.")
    except Exception:
        logger.exception("Failed to deliver reply for %s", consultation_id)
        await update.message.reply_text("Failed to send the reply (patient may have blocked the bot).")


async def stop_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id == ADMIN_CHAT_ID:
        pending_replies.pop(ADMIN_CHAT_ID, None)
        await update.message.reply_text("Reply cancelled.")


async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback: /reply CONSULTATION_ID message text"""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply CONSULTATION_ID your message")
        return
    consultation_id, message = context.args[0], " ".join(context.args[1:])
    patient_chat_id = get_patient_chat_id(consultation_id)
    if not patient_chat_id:
        await update.message.reply_text(f"Could not find consultation {consultation_id}.")
        return
    try:
        await context.bot.send_message(
            chat_id=patient_chat_id,
            text=STRINGS["reply_prefix"] + message,
        )
        await update.message.reply_text("Reply sent to the patient.")
    except Exception:
        logger.exception("Failed to deliver /reply for %s", consultation_id)
        await update.message.reply_text("Failed to send the reply.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN environment variable is not set.")
    if not ADMIN_CHAT_ID:
        raise SystemExit("ADMIN_CHAT_ID environment variable is not set.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_age)],
            PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_problem)],
            SYMPTOMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_symptoms)],
            PAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_pain)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_duration)],
            FLAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_flags)],
            PHOTOS: [MessageHandler(~filters.COMMAND, got_photo)],
            REVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_review)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(admin_reply_button, pattern=r"^reply:"))
    app.add_handler(CommandHandler("reply", reply_command))
    app.add_handler(CommandHandler("stopreply", stop_reply))
    # Admin reply text handler must be added after the conversation handler
    # so it does not interfere with patient conversations.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_text))
    app.add_error_handler(error_handler)

    logger.info("Bot started (long polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
