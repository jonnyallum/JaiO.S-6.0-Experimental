"""
Telegram Notifier — instant workflow alerts to Jonny's Telegram.
Non-blocking: send() catches all exceptions and returns bool.
"""
import httpx
import structlog

from config.settings import settings

log = structlog.get_logger()


class TelegramNotifier:
    def __init__(self):
        self._base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self._chat_id = settings.telegram_allowed_chat_id

    def send(self, text: str, silent: bool = False) -> bool:
        """Send a message. Returns True on success. Never raises."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self._base}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": f"🤖 <b>Jai.OS 6.0</b>\n\n{text}",
                        "parse_mode": "HTML",
                        "disable_notification": silent,
                    },
                )
                resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("telegram_send_failed", error=str(exc))
            return False

    def workflow_started(self, workflow_id: str, description: str) -> None:
        self.send(
            f"▶️ <b>Workflow started</b>\n"
            f"<code>{workflow_id[:8]}</code>\n"
            f"{description}",
            silent=True,
        )

    def workflow_completed(self, workflow_id: str, duration_s: float) -> None:
        self.send(
            f"✅ <b>Workflow completed</b>\n"
            f"<code>{workflow_id[:8]}</code>\n"
            f"⏱ {duration_s:.1f}s",
            silent=True,
        )

    def workflow_failed(self, workflow_id: str, error: str) -> None:
        self.send(
            f"❌ <b>Workflow FAILED</b>\n"
            f"<code>{workflow_id[:8]}</code>\n"
            f"<pre>{error[:400]}</pre>",
        )

    def agent_error(self, agent: str, repo: str, error: str) -> None:
        self.send(
            f"⚠️ <b>@{agent} error</b>\n"
            f"Repo: <code>{repo}</code>\n"
            f"<pre>{error[:300]}</pre>",
        )

    def alert(self, message: str) -> None:
        """Generic alert."""
        self.send(message)
