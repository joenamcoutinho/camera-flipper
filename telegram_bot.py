"""
Phase 1 Telegram scout. Polls your saved eBay searches on a timer,
scores anything new, and pushes it to you with Buy/Pass/Unsure buttons.
No LLM anywhere in this file - the "AI score" is scoring.py's formula.

Run once you've filled in .env (EBAY_CLIENT_ID/SECRET, TELEGRAM_BOT_TOKEN,
TELEGRAM_OWNER_ID). Until then, use `python cli.py` against sample data.

    python telegram_bot.py
"""
import os

from dotenv import load_dotenv
load_dotenv()  # must run before importing ebay_client, which reads EBAY_ENV at import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from db import (
    get_conn, init_db, save_listing, save_decision, listing_seen,
    get_camera_knowledge, record_your_decision, add_decision_note,
)
from signals import extract_features
from scoring import score_listing, max_bid
from cli import guess_seller_type, guess_camera_model  # reuse Phase 1 heuristics
import ebay_client

OWNER_ID = int(os.environ["TELEGRAM_OWNER_ID"])
QUERIES = [q.strip() for q in os.environ.get("SEARCH_QUERIES", "").split(",") if q.strip()]
POLL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))

# Comments are entirely optional (your call, not a required field). This
# just remembers which decision your *next* text message should attach
# to, so you can type a reason right after swiping if you feel like it -
# and it's forgotten as soon as you swipe again, so a random later
# message never gets misfiled onto an old decision.
_awaiting_comment: dict[int, int] = {}  # chat_id -> decision_id


def format_message(listing: dict, model: str, result: dict) -> str:
    badge = {"BUY": "\U0001F7E2 BUY", "UNSURE": "\U0001F7E1 UNSURE", "PASS": "\U0001F534 PASS"}[result["recommendation"]]
    lo, hi = result["resale_estimate"]
    resale = f"£{lo:.0f}-{hi:.0f}" if lo else "unknown (no camera_knowledge row)"
    return (
        f"<b>{model or listing['title']}</b>\n"
        f"<a href=\"{listing['ebay_url']}\">View on eBay</a>\n"
        f"eBay price: £{listing['price']:.0f}\n"
        f"Estimated resale: {resale}\n"
        f"Expected profit: £{result['expected_value']:.2f} ({result['roi']*100:.0f}% ROI)\n"
        f"Confidence: {result['confidence']*100:.0f}%\n\n"
        f"{badge}\n\n"
        f"{result['reasoning']}"
    )


async def poll_and_notify(context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    for query in QUERIES:
        try:
            items = ebay_client.search_items(query, price_max=60)
        except Exception as e:
            await context.bot.send_message(OWNER_ID, f"Search failed for '{query}': {e}")
            continue

        for item in items:
            listing = ebay_client.to_internal_listing(item)
            if listing_seen(conn, listing["ebay_item_id"]):
                continue

            features = extract_features(listing["title"], listing["description"])
            seller_type = guess_seller_type(listing)
            model = guess_camera_model(listing["title"])
            camera = get_camera_knowledge(conn, model) if model else None

            result = score_listing(listing["price"], camera, features, seller_type)
            save_listing(conn, {**listing, "camera_model_guess": model}, features)
            decision_id = save_decision(conn, listing["ebay_item_id"], result)

            if result["recommendation"] == "PASS":
                continue  # don't spam you with obvious passes

            buttons = [
                [InlineKeyboardButton("\U0001F517 View on eBay", url=listing["ebay_url"])],
                [
                    InlineKeyboardButton("❤️ Buy", callback_data=f"buy:{decision_id}"),
                    InlineKeyboardButton("❌ Pass", callback_data=f"pass:{decision_id}"),
                    InlineKeyboardButton("\U0001F937 Unsure", callback_data=f"unsure:{decision_id}"),
                ],
            ]
            await context.bot.send_message(
                OWNER_ID,
                format_message(listing, model, result),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
    conn.commit()
    conn.close()


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, decision_id = query.data.split(":")
    decision_id = int(decision_id)

    conn = get_conn()
    record_your_decision(conn, decision_id, action.upper())
    conn.commit()
    conn.close()

    await query.edit_message_reply_markup(reply_markup=None)
    _awaiting_comment[query.message.chat_id] = decision_id
    await query.message.reply_text(
        f"Logged: {action.upper()}. Reply here if you want to say why (totally optional) - "
        f"otherwise just carry on, this expires as soon as the next one comes in."
    )


async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    decision_id = _awaiting_comment.pop(chat_id, None)
    if decision_id is None:
        return  # no swipe is waiting on a comment right now - ignore stray messages
    conn = get_conn()
    add_decision_note(conn, decision_id, update.message.text)
    conn.commit()
    conn.close()
    await update.message.reply_text("Noted, thanks.")


def main():
    init_db()
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment))
    app.job_queue.run_repeating(poll_and_notify, interval=POLL_MINUTES * 60, first=5)
    print(f"Polling {len(QUERIES)} saved searches every {POLL_MINUTES} min. Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
