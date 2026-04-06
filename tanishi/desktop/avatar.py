"""
Tanishi Avatar Launcher — She has a face now.

Opens a small always-on-top window with an animated avatar
that reacts to Tanishi's current state.

States:
- idle: calm, slow blink, soft glow
- listening: eyes wide, ring brightens
- thinking: eyes squint, ring spins gold
- speaking: mouth animates, eyes engaged
- sarcastic: one eye squints, smirk, red tint
- happy: curved eyes, smile, green tint
- error: red flash, alert
- sleeping: dim, eyes closed

Usage: python -m tanishi.desktop.avatar
Double-click the face to toggle demo mode.
Eyes follow your mouse cursor.
"""

import os
import sys
import json
import asyncio
import threading
import webbrowser
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler


# Serve the avatar HTML locally
class AvatarHandler(SimpleHTTPRequestHandler):
    """Serve the avatar HTML with CORS and state endpoint."""

    def __init__(self, *args, state_holder=None, **kwargs):
        self.state_holder = state_holder
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == '/state':
            # Return current state as JSON
            state = self.state_holder.get('state', 'idle') if self.state_holder else 'idle'
            status_text = self.state_holder.get('status', 'ONLINE') if self.state_holder else 'ONLINE'
            bubble = self.state_holder.get('bubble', '') if self.state_holder else ''

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'state': state,
                'status': status_text,
                'bubble': bubble,
            }).encode())
            return

        if self.path == '/' or self.path == '/avatar':
            avatar_path = Path(__file__).parent / 'avatar.html'
            if avatar_path.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(avatar_path.read_bytes())
                return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def run_avatar_server(port=8899, state_holder=None):
    """Run a tiny HTTP server for the avatar."""
    handler = lambda *args, **kwargs: AvatarHandler(*args, state_holder=state_holder, **kwargs)
    server = HTTPServer(('127.0.0.1', port), handler)
    server.serve_forever()


def launch_avatar_window(port=8899):
    """Open the avatar in a small always-on-top window."""

    # Try pywebview first (best — proper small window)
    try:
        import webview
        webview.create_window(
            'Tanishi',
            f'http://127.0.0.1:{port}',
            width=320,
            height=380,
            resizable=False,
            on_top=True,
            frameless=False,
        )
        webview.start()
        return
    except ImportError:
        pass

    # Fallback: open in browser (works everywhere)
    print(f"  For a proper floating window: pip install pywebview")
    print(f"  Opening in browser instead...\n")
    webbrowser.open(f'http://127.0.0.1:{port}')

    # Keep the process alive
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main():
    """Launch the Tanishi avatar."""
    from dotenv import load_dotenv
    load_dotenv()

    port = 8899
    state_holder = {'state': 'idle', 'status': 'ONLINE', 'bubble': ''}

    print(f"\n👤 Tanishi Avatar starting on port {port}...")
    print(f"   Double-click the face for demo mode.")
    print(f"   Eyes follow your mouse cursor.\n")

    # Start HTTP server in background
    server_thread = threading.Thread(
        target=run_avatar_server,
        args=(port, state_holder),
        daemon=True,
    )
    server_thread.start()

    # Launch the window
    launch_avatar_window(port)


if __name__ == '__main__':
    main()
