#!/usr/bin/env python3
"""
SportyBet Nigeria Telegram Bot
Scrapes matches, lets users pick selections, generates booking codes.
"""

import logging
import os
from dotenv import load_dotenv
load_dotenv()
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)
from scraper import ScraperError
from scraper import SportyBetScraper
from booking import BookingCodeGenerator

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States
SELECTING_MATCHES = 1

# Per-user selections: {user_id: {event_id: {match_info}}}
user_selections = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - welcome message."""
    await update.message.reply_text(
        "🌟 *Welcome to AuraOdds Bot!*\n\n"
        "I fetch today's football matches from SportyBet Nigeria and generate real booking codes for you.\n\n"
        "Commands:\n"
        "🔍 /matches - Browse today's matches\n"
        "🎯 /myselections - View your current selections\n"
        "🎟️ /generate - Generate your booking code\n"
        "🗑️ /clear - Clear all selections\n"
        "❓ /help - How to use this bot",
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *How to use this bot:*\n\n"
        "1. Type /matches to see today's football fixtures\n"
        "2. Tap a match to expand its markets (1X2, BTTS, Over/Under)\n"
        "3. Tap an odds button to add it to your betslip\n"
        "4. Repeat for all matches you want\n"
        "5. Type /generate to create your SportyBet booking code\n"
        "6. Copy the code and use it on SportyBet Nigeria!\n\n"
        "⚠️ *Note:* Odds are scraped live and may change slightly.",
        parse_mode='Markdown'
    )


async def fetch_and_show_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch matches and show paginated list."""
    msg = await update.message.reply_text("⏳ Fetching today's matches from SportyBet Nigeria...")

    scraper = SportyBetScraper()
    try:
        matches = await scraper.get_today_matches()
    except ScraperError as e:
        logger.error(f"ScraperError: {e}")
        await msg.edit_text(
            f"❌ Could not fetch matches right now.\n"
            f"Reason: {e.message}\n\n"
            f"Please try again in a moment."
        )
        return
    except Exception as e:
        logger.error(f"Unexpected scraper error: {e}")
        await msg.edit_text("❌ Unexpected error fetching matches. Please try again.")
        return

    # Store matches in context for pagination
    context.user_data['matches'] = matches
    context.user_data['page'] = 0

    await msg.delete()
    await show_matches_page(update, context, page=0, matches=matches)


async def show_matches_page(update, context, page, matches, edit_message=None):
    """Show a page of matches."""
    ITEMS_PER_PAGE = 8
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_matches = matches[start_idx:end_idx]
    total_pages = (len(matches) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    user_id = update.effective_user.id
    user_sel = user_selections.get(user_id, {})

    keyboard = []
    for match in page_matches:
        # Show ✅ if any selection from this match is in user's betslip
        is_selected = any(
            sel.get('event_id') == match['event_id']
            for sel in user_sel.values()
        )
        prefix = "✅ " if is_selected else ""
        btn_text = f"{prefix}{match['home']} vs {match['away']}"
        keyboard.append([
            InlineKeyboardButton(btn_text, callback_data=f"match_{match['event_id']}")
        ])

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"page_{page-1}"))
    nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if end_idx < len(matches):
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"page_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([
        InlineKeyboardButton(f"🎯 My Selections ({len(user_sel)})", callback_data="view_selections"),
        InlineKeyboardButton("🎟️ Generate Code", callback_data="generate_code")
    ])

    markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"⚽ *Today's Football Matches* ({len(matches)} total)\n"
        f"Page {page+1} of {total_pages}\n\n"
        f"Tap a match to see markets & odds:"
    )

    if edit_message:
        await edit_message.edit_text(text, reply_markup=markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button presses."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data == "noop":
        return

    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        context.user_data['page'] = page
        matches = context.user_data.get('matches', [])
        await show_matches_page(update, context, page=page, matches=matches, edit_message=query.message)

    elif data.startswith("match_"):
        event_id = data.split("_", 1)[1]
        matches = context.user_data.get('matches', [])
        match = next((m for m in matches if m['event_id'] == event_id), None)
        if match:
            current_page = context.user_data.get('page', 0)
            await show_match_markets(query, match, user_id, current_page)

    elif data.startswith("pick_"):
        parts = data.split("|")
        if len(parts) == 6:
            _, event_id, market, pick_value, odd, teams = parts
            # Safe split: find last "vs" to handle team names containing "vs"
            vs_idx = teams.rfind("vs")
            if vs_idx == -1:
                home, away = teams, "Away"
            else:
                home = teams[:vs_idx].strip()
                away = teams[vs_idx + 2:].strip()

            if user_id not in user_selections:
                user_selections[user_id] = {}

            sel_key = event_id
            user_selections[user_id][sel_key] = {
                'event_id': event_id,
                'market': market,
                'pick': pick_value,
                'odd': float(odd),
                'teams': f"{home} vs {away}"
            }
            total = len(user_selections[user_id])
            total_odd = 1.0
            for s in user_selections[user_id].values():
                total_odd *= s['odd']

            await query.edit_message_text(
                f"✅ *Added to betslip!*\n\n"
                f"Match: {home} vs {away}\n"
                f"Market: {market}\n"
                f"Pick: {pick_value} @ {odd}\n\n"
                f"📋 Total selections: {total}\n"
                f"💰 Combined odds: {total_odd:.2f}\n\n"
                f"Use /matches to add more or /generate to get your code!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back to Matches", callback_data=f"page_{context.user_data.get('page', 0)}"),
                    InlineKeyboardButton("🎟️ Generate Code", callback_data="generate_code")
                ]])
            )

    elif data == "clear_all":
        user_selections[user_id] = {}
        await query.edit_message_text(
            "🗑️ Betslip cleared!\n\nUse /matches to start fresh.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚽ Browse Matches", callback_data="page_0")
            ]])
        )

    elif data == "view_selections":
        await show_selections(query, user_id)

    elif data == "generate_code":
        await do_generate_code(query, user_id)

    elif data.startswith("remove_"):
        event_id = data.split("_", 1)[1]
        if user_id in user_selections and event_id in user_selections[user_id]:
            del user_selections[user_id][event_id]
        await show_selections(query, user_id)


async def show_match_markets(query, match, user_id, current_page: int = 0):
    """Show markets for a specific match."""
    keyboard = []

    # 1X2
    if match.get('odds_1x2'):
        o = match['odds_1x2']
        keyboard.append([InlineKeyboardButton("— 1X2 Market —", callback_data="noop")])
        keyboard.append([
            InlineKeyboardButton(f"1 ({o.get('home', '?')})", callback_data=f"pick_|{match['event_id']}|1X2|1|{o.get('home', '1')}|{match['home']}vs{match['away']}"),
            InlineKeyboardButton(f"X ({o.get('draw', '?')})", callback_data=f"pick_|{match['event_id']}|1X2|X|{o.get('draw', '1')}|{match['home']}vs{match['away']}"),
            InlineKeyboardButton(f"2 ({o.get('away', '?')})", callback_data=f"pick_|{match['event_id']}|1X2|2|{o.get('away', '1')}|{match['home']}vs{match['away']}"),
        ])

    # Over/Under 2.5
    if match.get('odds_ou'):
        o = match['odds_ou']
        keyboard.append([InlineKeyboardButton("— Over/Under 2.5 —", callback_data="noop")])
        keyboard.append([
            InlineKeyboardButton(f"Over 2.5 ({o.get('over', '?')})", callback_data=f"pick_|{match['event_id']}|OU25|Over|{o.get('over', '1')}|{match['home']}vs{match['away']}"),
            InlineKeyboardButton(f"Under 2.5 ({o.get('under', '?')})", callback_data=f"pick_|{match['event_id']}|OU25|Under|{o.get('under', '1')}|{match['home']}vs{match['away']}"),
        ])

    # BTTS
    if match.get('odds_btts'):
        o = match['odds_btts']
        keyboard.append([InlineKeyboardButton("— Both Teams to Score —", callback_data="noop")])
        keyboard.append([
            InlineKeyboardButton(f"Yes ({o.get('yes', '?')})", callback_data=f"pick_|{match['event_id']}|BTTS|Yes|{o.get('yes', '1')}|{match['home']}vs{match['away']}"),
            InlineKeyboardButton(f"No ({o.get('no', '?')})", callback_data=f"pick_|{match['event_id']}|BTTS|No|{o.get('no', '1')}|{match['home']}vs{match['away']}"),
        ])

    keyboard.append([InlineKeyboardButton("◀️ Back to Matches", callback_data=f"page_{current_page}")])

    user_sel = user_selections.get(user_id, {})
    current = user_sel.get(match['event_id'])
    current_text = f"\n✅ Current pick: {current['market']} → {current['pick']} @ {current['odd']}" if current else ""

    await query.edit_message_text(
        f"⚽ *{match['home']} vs {match['away']}*\n"
        f"🏆 {match.get('league', 'Football')}\n"
        f"🕐 {match.get('time', 'TBD')}\n"
        f"{current_text}\n\n"
        f"Select your pick:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_selections(query, user_id):
    """Show user's current selections."""
    sels = user_selections.get(user_id, {})
    if not sels:
        await query.edit_message_text(
            "📋 Your betslip is empty!\n\nUse /matches to pick some games.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚽ Browse Matches", callback_data="page_0")
            ]])
        )
        return

    text = "📋 *Your Selections:*\n\n"
    total_odd = 1.0
    keyboard = []

    for event_id, sel in sels.items():
        text += f"⚽ {sel['teams']}\n"
        text += f"   {sel['market']}: *{sel['pick']}* @ {sel['odd']}\n\n"
        total_odd *= sel['odd']
        keyboard.append([
            InlineKeyboardButton(f"❌ Remove: {sel['teams'][:25]}...", callback_data=f"remove_{event_id}")
        ])

    text += f"💰 *Combined Odds: {total_odd:.2f}*\n"
    text += f"🎯 Total Picks: {len(sels)}"

    keyboard.append([
        InlineKeyboardButton("🎟️ Generate Booking Code", callback_data="generate_code"),
        InlineKeyboardButton("⚽ Add More", callback_data="page_0")
    ])

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


async def do_generate_code(query, user_id):
    """Generate SportyBet booking code."""
    sels = user_selections.get(user_id, {})
    if not sels:
        await query.edit_message_text(
            "❌ No selections yet! Use /matches to pick some games first."
        )
        return

    await query.edit_message_text("⏳ Generating your SportyBet booking code...")

    generator = BookingCodeGenerator()
    result = await generator.generate(list(sels.values()))

    if result['success']:
        code = result['code']
        total_odd = 1.0
        for s in sels.values():
            total_odd *= s['odd']

        text = (
            f"🎟️ *Your SportyBet Booking Code:*\n\n"
            f"`{code}`\n\n"
            f"💰 Combined Odds: *{total_odd:.2f}*\n"
            f"🎯 Selections: *{len(sels)}*\n\n"
            f"*How to use:*\n"
            f"1. Go to SportyBet Nigeria\n"
            f"2. Tap 'Load Booking Code'\n"
            f"3. Enter: `{code}`\n"
            f"4. Place your bet!\n\n"
            f"🔗 [Open SportyBet Nigeria](https://www.sportybet.com/ng/)"
        )
        keyboard = [[
            InlineKeyboardButton("🔄 New Selections", callback_data="page_0"),
            InlineKeyboardButton("🗑️ Clear All", callback_data="clear_all")
        ]]
    else:
        text = (
            f"❌ *Could not generate code*\n\n"
            f"Reason: {result.get('error', 'Unknown error')}\n\n"
            f"Please try again or check that the matches are still available."
        )
        keyboard = [[InlineKeyboardButton("🔄 Try Again", callback_data="generate_code")]]

    await query.edit_message_text(text, parse_mode='Markdown',
                                   reply_markup=InlineKeyboardMarkup(keyboard),
                                   disable_web_page_preview=True)


async def my_selections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show selections via command."""
    user_id = update.effective_user.id
    sels = user_selections.get(user_id, {})
    if not sels:
        await update.message.reply_text("📋 Your betslip is empty! Use /matches to pick games.")
        return

    text = "📋 *Your Selections:*\n\n"
    total_odd = 1.0
    for sel in sels.values():
        text += f"⚽ {sel['teams']}\n   {sel['market']}: *{sel['pick']}* @ {sel['odd']}\n\n"
        total_odd *= sel['odd']
    text += f"💰 *Combined Odds: {total_odd:.2f}*"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton("🎟️ Generate Code", callback_data="generate_code")
    ]]))


async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate command shortcut."""
    user_id = update.effective_user.id
    sels = user_selections.get(user_id, {})
    if not sels:
        await update.message.reply_text("❌ No selections! Use /matches first.")
        return

    msg = await update.message.reply_text("⏳ Generating booking code...")
    generator = BookingCodeGenerator()
    result = await generator.generate(list(sels.values()))

    if result['success']:
        code = result['code']
        total_odd = 1.0
        for s in sels.values():
            total_odd *= s['odd']
        await msg.edit_text(
            f"🎟️ *Booking Code:* `{code}`\n\n"
            f"💰 Odds: *{total_odd:.2f}* | 🎯 Picks: *{len(sels)}*\n\n"
            f"[Open SportyBet Nigeria](https://www.sportybet.com/ng/)",
            parse_mode='Markdown', disable_web_page_preview=True
        )
    else:
        await msg.edit_text(f"❌ Failed: {result.get('error', 'Unknown error')}")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all selections."""
    user_id = update.effective_user.id
    user_selections[user_id] = {}
    await update.message.reply_text("🗑️ Betslip cleared! Use /matches to start fresh.")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: Set TELEGRAM_BOT_TOKEN environment variable")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("matches", fetch_and_show_matches))
    app.add_handler(CommandHandler("myselections", my_selections))
    app.add_handler(CommandHandler("generate", generate_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 SportyBet Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
