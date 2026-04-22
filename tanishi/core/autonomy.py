"""
Tanishi Autonomy Engine — She works while you sleep.

A background daemon that:
1. Runs scheduled tasks (check email, news, repos)
2. Monitors for events (file changes, system alerts)
3. Sends proactive notifications
4. Runs self-improvement scans overnight

This is what separates a chatbot from JARVIS.
Tanishi doesn't wait to be asked — she anticipates.
"""

import json
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    """A task that runs on a schedule."""
    id: str
    name: str
    description: str
    command: str  # What to tell the brain to do
    interval_minutes: int = 60  # How often to run
    enabled: bool = True
    last_run: str = ""
    next_run: str = ""
    run_count: int = 0
    results_history: list[str] = field(default_factory=list)


@dataclass
class Notification:
    """A proactive notification from Tanishi."""
    id: str
    message: str
    priority: str = "normal"  # "low", "normal", "high", "urgent"
    source: str = ""  # Which task generated this
    timestamp: str = ""
    read: bool = False


class AutonomyEngine:
    """
    The background brain of Project Tanishi.

    Manages scheduled tasks, monitors, and proactive notifications.
    Runs alongside the main CLI/API in a background thread.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.tasks_file = data_dir / "scheduled_tasks.json"
        self.notifications_file = data_dir / "notifications.json"
        self.tasks: dict[str, ScheduledTask] = {}
        self.notifications: list[Notification] = []
        self._running = False
        self._brain_callback: Optional[Callable] = None
        self._notify_callback: Optional[Callable] = None

        self._load_state()
        self._register_default_tasks()

    def _load_state(self):
        """Load saved tasks and notifications."""
        if self.tasks_file.exists():
            try:
                data = json.loads(self.tasks_file.read_text())
                for t in data:
                    task = ScheduledTask(**t)
                    self.tasks[task.id] = task
            except Exception as e:
                logger.warning("Failed to load tasks state from %s: %s", self.tasks_file, e)

        if self.notifications_file.exists():
            try:
                data = json.loads(self.notifications_file.read_text())
                self.notifications = [Notification(**n) for n in data[-50:]]  # Keep last 50
            except Exception as e:
                logger.warning(
                    "Failed to load notifications state from %s: %s",
                    self.notifications_file,
                    e,
                )

    def _save_state(self):
        """Save tasks and notifications to disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        tasks_data = [
            {
                "id": t.id, "name": t.name, "description": t.description,
                "command": t.command, "interval_minutes": t.interval_minutes,
                "enabled": t.enabled, "last_run": t.last_run,
                "next_run": t.next_run, "run_count": t.run_count,
                "results_history": t.results_history[-10:],
            }
            for t in self.tasks.values()
        ]
        self.tasks_file.write_text(json.dumps(tasks_data, indent=2))

        notif_data = [
            {
                "id": n.id, "message": n.message, "priority": n.priority,
                "source": n.source, "timestamp": n.timestamp, "read": n.read,
            }
            for n in self.notifications[-50:]
        ]
        self.notifications_file.write_text(json.dumps(notif_data, indent=2))

    def _register_default_tasks(self):
        """Register default scheduled tasks."""
        defaults = [
            ScheduledTask(
                id="morning_briefing",
                name="Morning Briefing",
                description="Summarize today's schedule, news, and priorities",
                command="Give me a quick morning briefing: what day is it, any important news in AI, and remind me of anything I've told you about today's plans.",
                interval_minutes=720,  # Every 12 hours
                enabled=False,  # User must enable
            ),
            ScheduledTask(
                id="ai_news_scan",
                name="AI News Scanner",
                description="Scan for latest AI developments",
                command="Search for the top 3 most important AI news stories from today. Be brief.",
                interval_minutes=360,  # Every 6 hours
                enabled=False,
            ),
            ScheduledTask(
                id="self_improve_scan",
                name="Self-Improvement Scan",
                description="Look for new tools and capabilities to add",
                command="Scan GitHub for trending AI agent tools or MCP servers released in the last week that could improve your capabilities. List the top 3 with why they'd be useful.",
                interval_minutes=1440,  # Daily
                enabled=False,
            ),
            ScheduledTask(
                id="system_health",
                name="System Health Check",
                description="Monitor disk space, memory, and system health",
                command="Check the system info. If disk space is below 10% free or anything looks concerning, flag it. Otherwise just note that everything's normal.",
                interval_minutes=120,  # Every 2 hours
                enabled=False,
            ),
        ]

        for task in defaults:
            if task.id not in self.tasks:
                self.tasks[task.id] = task

        self._save_state()

    def set_brain_callback(self, callback: Callable):
        """Set the function to call when a task needs the brain."""
        self._brain_callback = callback

    def set_notify_callback(self, callback: Callable):
        """Set the function to call when there's a notification."""
        self._notify_callback = callback

    # ============================================================
    # Task Management
    # ============================================================

    def add_task(self, task: ScheduledTask) -> ScheduledTask:
        """Add or update a scheduled task."""
        self.tasks[task.id] = task
        self._save_state()
        return task

    def enable_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Enable a scheduled task."""
        if task_id in self.tasks:
            self.tasks[task_id].enabled = True
            now = datetime.now()
            self.tasks[task_id].next_run = (
                now + timedelta(minutes=1)
            ).isoformat()  # Run soon after enabling
            self._save_state()
            return self.tasks[task_id]
        return None

    def disable_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Disable a scheduled task."""
        if task_id in self.tasks:
            self.tasks[task_id].enabled = False
            self._save_state()
            return self.tasks[task_id]
        return None

    def remove_task(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        if task_id in self.tasks:
            del self.tasks[task_id]
            self._save_state()
            return True
        return False

    def list_tasks(self) -> list[ScheduledTask]:
        """List all tasks."""
        return list(self.tasks.values())

    # ============================================================
    # Notifications
    # ============================================================

    def add_notification(self, message: str, priority: str = "normal", source: str = ""):
        """Add a notification."""
        notif = Notification(
            id=f"notif_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            message=message,
            priority=priority,
            source=source,
            timestamp=datetime.now().isoformat(),
        )
        self.notifications.append(notif)
        self._save_state()

        if self._notify_callback:
            self._notify_callback(notif)

    def get_unread_notifications(self) -> list[Notification]:
        """Get all unread notifications."""
        return [n for n in self.notifications if not n.read]

    def mark_all_read(self):
        """Mark all notifications as read."""
        for n in self.notifications:
            n.read = True
        self._save_state()

    # ============================================================
    # Background Loop
    # ============================================================

    async def run_background(self):
        """
        Main background loop. Checks and runs due tasks.
        Call this in a background asyncio task.
        """
        self._running = True

        while self._running:
            try:
                now = datetime.now()

                for task in self.tasks.values():
                    if not task.enabled:
                        continue

                    # Check if task is due
                    is_due = False
                    if not task.next_run:
                        is_due = True
                    else:
                        try:
                            next_run = datetime.fromisoformat(task.next_run)
                            if now >= next_run:
                                is_due = True
                        except ValueError:
                            is_due = True

                    if is_due and self._brain_callback:
                        # Run the task
                        try:
                            result = await self._brain_callback(task.command)
                            task.last_run = now.isoformat()
                            task.next_run = (
                                now + timedelta(minutes=task.interval_minutes)
                            ).isoformat()
                            task.run_count += 1
                            task.results_history.append(
                                f"[{now.strftime('%H:%M')}] {result[:200]}"
                            )

                            # Create notification from result
                            self.add_notification(
                                message=f"**{task.name}**: {result[:300]}",
                                source=task.id,
                            )

                        except Exception as e:
                            task.results_history.append(
                                f"[{now.strftime('%H:%M')}] ERROR: {str(e)}"
                            )

                        self._save_state()

            except Exception as e:
                logger.warning("Autonomy background loop iteration failed: %s", e)

            # Check every 60 seconds
            await asyncio.sleep(60)

    def stop(self):
        """Stop the background loop."""
        self._running = False

    def get_status(self) -> dict:
        """Get autonomy engine status."""
        enabled_count = sum(1 for t in self.tasks.values() if t.enabled)
        unread = len(self.get_unread_notifications())
        return {
            "running": self._running,
            "total_tasks": len(self.tasks),
            "enabled_tasks": enabled_count,
            "unread_notifications": unread,
        }
