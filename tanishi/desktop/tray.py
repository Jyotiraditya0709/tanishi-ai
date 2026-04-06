"""
Tanishi System Tray — She lives in your taskbar.

Always-on presence with:
- System tray icon with status
- Desktop notifications from autonomy engine
- Quick actions (voice mode, dashboard, status)
- Background monitoring

Setup: pip install pystray Pillow
Run: python -m tanishi.desktop.tray
"""

import os
import sys
import asyncio
import threading
import webbrowser
from pathlib import Path
from datetime import datetime


def run_tray():
    """Run the system tray icon."""
    try:
        import pystray
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Install dependencies: pip install pystray Pillow")
        return

    from tanishi.core import get_config
    config = get_config()

    # Create a simple icon (cyan 'T' on dark background)
    def create_icon_image():
        img = Image.new("RGB", (64, 64), (15, 15, 25))
        draw = ImageDraw.Draw(img)
        # Draw a big T
        try:
            font = ImageFont.truetype("arial.ttf", 40)
        except Exception:
            font = ImageFont.load_default()
        draw.text((18, 8), "T", fill=(0, 200, 255), font=font)
        return img

    def on_dashboard(icon, item):
        """Open the web dashboard."""
        webbrowser.open(f"http://localhost:{config.port}")

    def on_cli(icon, item):
        """Open CLI in a new terminal."""
        if sys.platform == "win32":
            os.system('start cmd /k "python -m tanishi.cli"')
        else:
            os.system('x-terminal-emulator -e "python -m tanishi.cli" &')

    def on_voice(icon, item):
        """Open CLI in voice mode."""
        if sys.platform == "win32":
            os.system('start cmd /k "python -c \\"from tanishi.cli import TanishiCLI; import asyncio; cli = TanishiCLI(); asyncio.run(cli._start_voice_mode())\\""')

    def on_status(icon, item):
        """Show status notification."""
        from tanishi.core.brain import TanishiBrain
        from tanishi.memory.manager import MemoryManager

        brain = TanishiBrain()
        memory = MemoryManager(config.db_path)
        status = brain.get_status()
        mem_stats = memory.get_stats()

        icon.notify(
            f"Claude: {status['claude']}\n"
            f"Tools: {status['tools']}\n"
            f"Memories: {mem_stats['total_memories']}",
            "Tanishi Status"
        )

    def on_quit(icon, item):
        """Quit the tray icon."""
        icon.stop()

    # Build menu
    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", on_dashboard, default=True),
        pystray.MenuItem("Open CLI", on_cli),
        pystray.MenuItem("Status", on_status),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon(
        "Tanishi",
        create_icon_image(),
        "Tanishi — Personal AI",
        menu,
    )

    print("🔵 Tanishi is now in your system tray!")
    print("   Right-click the 'T' icon for options.")
    print("   Double-click to open the dashboard.\n")

    # Start notification watcher in background
    notification_thread = threading.Thread(
        target=_watch_notifications, args=(icon, config), daemon=True
    )
    notification_thread.start()

    icon.run()


def _watch_notifications(icon, config):
    """Background thread that watches for new notifications and shows them."""
    import time
    import json

    notif_file = config.tanishi_home / "notifications.json"
    last_count = 0

    while True:
        try:
            if notif_file.exists():
                data = json.loads(notif_file.read_text())
                unread = [n for n in data if not n.get("read", True)]

                if len(unread) > last_count:
                    # New notifications!
                    for notif in unread[last_count:]:
                        try:
                            icon.notify(
                                notif.get("message", "New notification")[:200],
                                f"Tanishi — {notif.get('source', 'Alert')}"
                            )
                        except Exception:
                            pass

                last_count = len(unread)

        except Exception:
            pass

        time.sleep(30)  # Check every 30 seconds


def main():
    from dotenv import load_dotenv
    load_dotenv()
    run_tray()


if __name__ == "__main__":
    main()
