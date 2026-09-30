# Orald_bot - Telegram Dental Consultation Bot

A simple, professional MVP for a dental consultation service on Telegram
(@Orald_bot). Patients describe their dental problem, answer short triage
questions and upload photos; the dentist receives a formatted consultation
and can reply to the patient through the bot.

**Medical safety:** the bot does not diagnose. It collects information for a
licensed dentist to review manually, and shows every patient a disclaimer
advising urgent care for emergencies.

## Features

- `/start` guided consultation flow:
  name -> age -> main problem -> symptoms -> pain severity -> duration ->
  swelling/fever/bleeding/trauma flags -> 1-5 photos -> review -> submit
- Consultation sent to the dentist with photos and a **Reply to patient** button
- Fallback text command: `/reply CONSULTATION_ID message`
- Minimal SQLite storage (`consultations.db`) - no servers, no Docker, no webhooks
- Long polling - works on any always-on machine, including free hosting tiers
- All patient-facing text is centralized in a `STRINGS` dict so Arabic can be
  added easily later

## Setup

### 1. Clone and install

```bash
git clone https://github.com/hossamaldaoos21-maker/-Orald_bot.git
cd -Orald_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

- `BOT_TOKEN` - from @BotFather on Telegram
- `ADMIN_CHAT_ID` - your own Telegram numeric ID (ask @userinfobot)

Load them before running:

```bash
export $(grep -v '^#' .env | xargs)   # Linux/macOS
```

On hosting services (Railway, Render, PythonAnywhere, etc.) set the same
variables in the service's environment/settings panel instead of a `.env` file.

### 3. Run

```bash
python bot.py
```

The bot runs with long polling, so it works behind NAT and needs no public URL.
Keep the process running (e.g. `nohup python bot.py &`, systemd, or a hosting
service's "worker" process type).

## How the dentist replies

1. A consultation arrives with a **Reply to patient** button - tap it and type
   your reply; it is delivered to the patient.
2. Or use `/reply C-XXXXXXXX Your message here`.
3. `/stopreply` aborts a pending reply.

## Project layout

| File | Purpose |
| --- | --- |
| `bot.py` | The whole bot (conversation flow, admin reply, storage) |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for required environment variables |
| `consultations.db` | SQLite database, created automatically on first run |

## Extending later

- **Arabic support:** add a `STRINGS_AR` dict and a language-choice step at
  `/start`, then read strings from the selected dict.
- **Clinic referrals:** add a final step after submission offering clinic
  location/appointment info.
- **Storage:** swap the two SQLite helper functions for a real database if the
  volume grows.
