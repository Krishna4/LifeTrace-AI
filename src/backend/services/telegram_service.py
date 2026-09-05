import os
import re
import logging
import requests
from datetime import date
from typing import Any, Dict, List, Optional
from src.backend.database.sqlite import get_personal_events, get_transactions
from src.backend.query.slm_router import route_query_slm
from src.backend.query.search_engine import execute_unified_search

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def get_telegram_config() -> Dict[str, Optional[str]]:
    """Fetches Telegram bot token and default chat ID from environment."""
    return {
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None,
        "default_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None,
    }


def send_telegram_message(
    text: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: str = "Markdown",
) -> Dict[str, Any]:
    """
    Sends a message to a Telegram chat using the Telegram Bot API.
    """
    config = get_telegram_config()
    token = bot_token or config["bot_token"]
    target_chat_id = chat_id or config["default_chat_id"]

    if not token:
        logger.warning("Telegram notification skipped: TELEGRAM_BOT_TOKEN not configured.")
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    if not target_chat_id:
        logger.warning("Telegram notification skipped: TELEGRAM_CHAT_ID not configured.")
        return {"success": False, "error": "TELEGRAM_CHAT_ID not configured"}

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        res_data = response.json()
        if response.status_code == 200 and res_data.get("ok"):
            logger.info(f"✅ Telegram message successfully delivered to chat '{target_chat_id}'")
            return {"success": True, "message_id": res_data.get("result", {}).get("message_id")}
        else:
            # If Markdown parsing failed, retry as plain text
            if "can't parse entities" in res_data.get("description", "").lower():
                payload.pop("parse_mode", None)
                retry_res = requests.post(url, json=payload, timeout=10)
                if retry_res.status_code == 200:
                    return {"success": True, "message_id": retry_res.json().get("result", {}).get("message_id")}
            logger.error(f"❌ Telegram API Error: {res_data.get('description')}")
            return {"success": False, "error": res_data.get("description")}
    except Exception as e:
        logger.error(f"❌ Error dispatching Telegram message: {e}")
        return {"success": False, "error": str(e)}


def format_event_for_telegram(event: Dict[str, Any]) -> str:
    """Formats a single personal event for clean Telegram Markdown display."""
    cat = event.get("category", "DAILY_EVENT").upper()
    title = event.get("title", "Untitled Event")
    ev_date = event.get("event_date", str(date.today()))
    location = event.get("location")
    person = event.get("entity_person")
    details = event.get("details")

    icons = {
        "MEETING": "👥",
        "TRAVEL": "✈️",
        "HEALTH": "🏥",
        "DINING": "🍽️",
        "MILESTONE": "🏆",
        "REMINDER": "⏰",
        "DAILY_EVENT": "📌",
    }
    icon = icons.get(cat, "📌")

    lines = [f"{icon} *[{cat}]* *{title}*"]
    lines.append(f"📅 *Date:* `{ev_date}`")
    if location:
        lines.append(f"📍 *Location:* {location}")
    if person:
        lines.append(f"👤 *Person/With:* {person}")
    if details:
        lines.append(f"📝 *Details:* _{details}_")

    return "\n".join(lines)


def publish_event_to_telegram(
    event: Dict[str, Any],
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Publishes an immediate Telegram notification for a newly logged event."""
    text = "🌱 *LifeTrace AI — New Event Logged:*\n\n" + format_event_for_telegram(event)
    return send_telegram_message(text, bot_token=bot_token, chat_id=chat_id)


def publish_daily_digest(
    username: str = "default_user",
    target_date: Optional[date] = None,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches all scheduled events and tasks for the day and sends a formatted morning/evening digest.
    """
    check_date = target_date or date.today()
    events = get_personal_events(
        event_date=check_date,
        username=username,
        include_secure=True,
        limit=50,
        db_path=db_path,
    )


    date_str = check_date.strftime("%A, %B %d, %Y")

    if not events:
        msg = (
            f"🌱 *LifeTrace AI Daily Agenda*\n"
            f"📅 *{date_str}* (User: `{username}`)\n\n"
            f"🎉 _No events or meetings scheduled for today. Enjoy your day!_"
        )
    else:
        event_blocks = []
        for idx, ev in enumerate(events, 1):
            event_blocks.append(f"*{idx}.* " + format_event_for_telegram(ev.model_dump()))

        msg = (
            f"🌱 *LifeTrace AI Daily Agenda*\n"
            f"📅 *{date_str}* (User: `{username}`)\n"
            f"🔔 *{len(events)} event(s) scheduled:*\n\n"
            + "\n\n".join(event_blocks)
            + "\n\n_Reply to this chat to ask questions or log new activities!_"
        )

    res = send_telegram_message(msg, bot_token=bot_token, chat_id=chat_id)
    return {
        "date": check_date.isoformat(),
        "events_count": len(events),
        "delivery": res,
    }


def handle_telegram_update(update: Dict[str, Any], default_user: str = "default_user") -> Optional[Dict[str, Any]]:
    """
    Processes an incoming Telegram Webhook update payload.
    Supports slash commands (/today, /events, /expenses, /help) and natural language RAG queries.
    """
    message = (
        update.get("message") 
        or update.get("edited_message") 
        or update.get("channel_post") 
        or update.get("edited_channel_post")
    )
    if not message or "text" not in message:
        return None


    chat_id = str(message.get("chat", {}).get("id"))
    user_text = message.get("text", "").strip()
    sender_name = message.get("from", {}).get("first_name", default_user)

    logger.info(f"📨 Telegram message from '{sender_name}' ({chat_id}): '{user_text}'")

    # Command Handling
    if user_text.startswith("/start") or user_text.startswith("/help"):
        reply = (
            f"👋 *Hello {sender_name}! Welcome to LifeTrace AI.*\n\n"
            f"I am your personal intelligence ledger and assistant.\n\n"
            f"🎯 *Available Commands:*\n"
            f"• `/today` — View today's scheduled life events & agenda\n"
            f"• `/events` — Show your recent events & meetings\n"
            f"• `/expenses` — Show your recent transactions & expenses\n"
            f"• `/digest` — Send full today's agenda briefing\n\n"
            f"💬 *Natural Language Queries:*\n"
            f"You can ask me anything directly, for example:\n"
            f"• _\"What meetings do I have today?\"_\n"
            f"• _\"How much did I pay Alex?\"_\n"
            f"• _\"Show my travel details\"_\n"
            f"• _\"Paid 500 rupees at Starbucks for coffee\"_ (automatically logged!)"
        )
        send_telegram_message(reply, chat_id=chat_id)
        return {"action": "help", "chat_id": chat_id}

    elif user_text.startswith("/today"):
        today = date.today()
        evs = get_personal_events(event_date=today, username=default_user, limit=10)
        if not evs:
            reply = f"📅 *No events scheduled for today* (`{today.isoformat()}`)."
        else:
            lines = [f"📅 *Today's Events ({today.isoformat()}):*"]
            for ev in evs:
                lines.append(f"• *[{ev.category}]* {ev.title} ({ev.location or 'No location'})")
            reply = "\n".join(lines)
        send_telegram_message(reply, chat_id=chat_id)
        return {"action": "today", "events_count": len(evs)}

    elif user_text.startswith("/events"):
        evs = get_personal_events(username=default_user, limit=10)
        if not evs:
            reply = "📅 *No personal events found in your ledger.*"
        else:
            lines = ["📅 *Recent Personal Life Events:*"]
            for ev in evs:
                lines.append(f"• *[{ev.category}]* {ev.title} — `{ev.event_date}`")
            reply = "\n".join(lines)
        send_telegram_message(reply, chat_id=chat_id)
        return {"action": "events", "events_count": len(evs)}

    elif user_text.startswith("/expenses"):
        txs = get_transactions(username=default_user, limit=10)
        if not txs:
            reply = "💰 *No financial transactions found in your records.*"
        else:
            lines = ["💰 *Recent Financial Transactions:*"]
            for tx in txs:
                lines.append(f"• *{tx.entity_person}:* ${tx.amount:.2f} {tx.currency} on `{tx.transaction_date}`")
            reply = "\n".join(lines)
        send_telegram_message(reply, chat_id=chat_id)
        return {"action": "expenses", "txs_count": len(txs)}

    elif user_text.startswith("/digest"):
        res = publish_daily_digest(username=default_user, chat_id=chat_id)
        return {"action": "digest", "result": res}

    # Natural Language Processing via Query Router & Unified Search
    try:
        route = route_query_slm(user_text)
        search_res = execute_unified_search(user_text, route, username=default_user)
        answer = search_res.get("answer") or "I could not find relevant information in your records."

        reply = f"🌱 *LifeTrace AI:*\n\n{answer}"
        send_telegram_message(reply, chat_id=chat_id)
        return {"action": "query", "route": route.target_engine, "chat_id": chat_id}
    except Exception as e:
        logger.error(f"Error handling Telegram natural language query: {e}")
        send_telegram_message(f"⚠️ Error answering your request: {e}", chat_id=chat_id)
        return {"action": "error", "error": str(e)}
