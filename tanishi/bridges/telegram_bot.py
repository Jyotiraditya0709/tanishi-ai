"""
Tanishi Telegram Bridge — Text her from your phone.

Setup:
1. Open Telegram, search for @BotFather
2. Send /newbot, name it "Tanishi", pick a username
3. Copy the bot token
4. Add to .env: TELEGRAM_BOT_TOKEN=your-token-here
5. Run: python -m tanishi.bridges.telegram_bot

Now text your bot on Telegram — Tanishi responds with full
tool access, personality, and memory.
"""

import os
import json
import asyncio
import logging
from uuid import uuid4
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# approval_id -> Future[bool] resolved by inline-button CallbackQueryHandler
_pending_approvals: dict[str, asyncio.Future] = {}

# Serializes set_approval_callback → brain.think() on the shared registry (personal bot).
# Revisit for multi-user: per-chat locks or contextvar-bound approvers.
_think_lock = asyncio.Lock()


def make_telegram_approver(bot, chat_id: int):
    """Build an async registry approval callback for one chat turn."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    async def approve(tool_name: str, tool_input: dict) -> bool:
        approval_id = uuid4().hex
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        _pending_approvals[approval_id] = fut

        payload = json.dumps(tool_input, indent=2)[:300]
        text = (
            f"⚠️ Tanishi wants to run *{tool_name}*\n"
            f"```\n{payload}\n```\n"
            "_Allow?_"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Allow", callback_data=f"appr:{approval_id}:yes"),
                InlineKeyboardButton("⛔ Deny", callback_data=f"appr:{approval_id}:no"),
            ]
        ])

        sent = None
        try:
            sent = await bot.send_message(
                chat_id, text, reply_markup=kb, parse_mode="Markdown"
            )
        except Exception:
            plain = (
                f"⚠️ Tanishi wants to run {tool_name}\n"
                f"{payload}\n\nAllow?"
            )
            sent = await bot.send_message(chat_id, plain, reply_markup=kb)

        try:
            return await asyncio.wait_for(fut, timeout=120)
        except asyncio.TimeoutError:
            if sent is not None:
                try:
                    await bot.edit_message_text(
                        "⌛ Approval timed out — denied.",
                        chat_id=chat_id,
                        message_id=sent.message_id,
                    )
                except Exception:
                    pass
            return False
        finally:
            _pending_approvals.pop(approval_id, None)

    return approve


async def run_telegram_bot():
    """Start the Telegram bot bridge."""
    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            MessageHandler,
            filters,
            ContextTypes,
        )
    except ImportError:
        print("Install telegram library: pip install python-telegram-bot")
        return

    from tanishi.core import get_config
    from tanishi.core.brain import TanishiBrain
    from tanishi.memory.manager import MemoryManager
    from tanishi.tools import register_all_tools
    from tanishi.tools.registry import ToolRegistry

    config = get_config()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")

    if not token:
        print("Set TELEGRAM_BOT_TOKEN in your .env file.")
        print("Get one from @BotFather on Telegram.")
        return

    # Allowed user IDs (security — only you can talk to your Tanishi)
    allowed_ids_str = os.getenv("TELEGRAM_ALLOWED_IDS", "")
    allowed_ids = set()
    if allowed_ids_str:
        allowed_ids = {int(x.strip()) for x in allowed_ids_str.split(",") if x.strip()}

    # Initialize brain
    registry = ToolRegistry()
    register_all_tools(None, registry)

    brain = TanishiBrain(tool_registry=registry)
    memory = MemoryManager(config.db_path)
    restored = brain.load_session_history("telegram", max_turns=30)
    if restored:
        print(f"[telegram] restored {restored} messages from session telegram")

    print(f"\n🤖 Tanishi Telegram Bridge starting...")
    print(f"   Brain: {brain.get_status()['claude']}")
    print(f"   Tools: {len(registry.tools)}")
    print(f"   Allowed users: {'all (set TELEGRAM_ALLOWED_IDS for security!)' if not allowed_ids else allowed_ids}")
    print(f"   Send a message to your bot on Telegram!\n")

    async def on_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Resolve a pending tool-approval Future from an inline button press."""
        q = update.callback_query
        user = update.effective_user
        if allowed_ids and user.id not in allowed_ids:
            await q.answer("Not authorized", show_alert=True)
            return

        await q.answer()
        try:
            _, approval_id, verdict = q.data.split(":")
        except ValueError:
            return

        fut = _pending_approvals.get(approval_id)
        allowed = verdict == "yes"
        if fut and not fut.done():
            fut.set_result(allowed)
        try:
            await q.edit_message_text("✅ Allowed." if allowed else "⛔ Denied.")
        except Exception:
            pass

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        if allowed_ids and user.id not in allowed_ids:
            await update.message.reply_text("Access denied. Your ID: " + str(user.id))
            return

        # Store telegram user info
        memory.set_core("telegram_user", user.first_name)

        await update.message.reply_text(
            f"Hey {user.first_name}! 👋\n\n"
            f"I'm Tanishi — your personal AI. Not a chatbot, not an assistant. "
            f"Think of me as the smartest friend you'll ever have.\n\n"
            f"Just text me anything. I can:\n"
            f"• Search the web\n"
            f"• Read/write files on your PC\n"
            f"• Run commands\n"
            f"• Remember everything you tell me\n"
            f"• Be delightfully sarcastic\n\n"
            f"Try: \"What's happening in AI today?\""
        )

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages."""
        user = update.effective_user
        if allowed_ids and user.id not in allowed_ids:
            await update.message.reply_text("Nice try. Access denied. 🚫")
            return

        user_text = update.message.text
        if not user_text:
            return

        # Show typing indicator
        await update.message.chat.send_action("typing")

        # Build context
        core_context = memory.build_core_context()
        tool_context = f"\n\nYou have {len(registry.tools)} tools. Use them when helpful."

        # One tool-capable turn at a time on the shared registry (personal bot).
        async with _think_lock:
            registry.set_approval_callback(
                make_telegram_approver(context.bot, update.effective_chat.id)
            )
            try:
                response = await brain.think(
                    user_input=user_text,
                    extra_context=core_context + tool_context,
                )
            finally:
                registry.set_approval_callback(None)

        # Log
        memory.log_message("telegram", "user", user_text)
        memory.log_message("telegram", "assistant", response.content, response.model_used)

        # Auto-learn
        try:
            from tanishi.memory.auto_learn import AutoMemory
            auto = AutoMemory(memory, brain.claude_client)
            await auto.extract_and_store(user_text, response.content)
        except Exception:
            pass

        # Send response (split if too long for Telegram)
        text = response.content
        tools_info = ""
        if response.tools_used:
            tools_info = f"\n\n🔧 _{', '.join(response.tools_used)}_"

        full_response = text + tools_info

        # Telegram max message length is 4096
        if len(full_response) <= 4096:
            await update.message.reply_text(full_response)
        else:
            # Split into chunks
            chunks = [full_response[i:i+4000] for i in range(0, len(full_response), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk)

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        status = brain.get_status()
        mem_stats = memory.get_stats()
        await update.message.reply_text(
            f"🧠 Tanishi Status\n\n"
            f"Claude: {status['claude']}\n"
            f"Ollama: {status['ollama']}\n"
            f"Tools: {status['tools']}\n"
            f"Memories: {mem_stats['total_memories']}\n"
            f"Core memories: {mem_stats['core_memories']}"
        )

    async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /memory command."""
        core = memory.get_all_core()
        recent = memory.get_recent_memories(5)

        text = "🧠 Memory\n\n**Core:**\n"
        for k, v in core.items():
            text += f"  {k}: {v}\n"

        text += "\n**Recent:**\n"
        for m in recent:
            text += f"  [{m.category}] {m.content}\n"

        await update.message.reply_text(text)

    # Build the bot
    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(on_approval_callback, pattern=r"^appr:"))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    print("✅ Telegram bot is running! Press Ctrl+C to stop.")

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(run_telegram_bot())


if __name__ == "__main__":
    main()
