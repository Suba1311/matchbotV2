"""Notifier selection by name — mirrors ``runtime.factory.get_runtime``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from matchbot.notify.base import LogNotifier, Notifier

if TYPE_CHECKING:
    from matchbot.config.settings import Settings


def get_notifier(settings: Settings) -> Notifier:
    """Return the notifier configured via ``MATCHBOT_NOTIFIER`` (log | ses)."""
    key = settings.notifier.lower().strip()
    if key == "log":
        return LogNotifier()
    if key == "ses":
        from matchbot.notify.ses import SESNotifier

        if not settings.ses_sender:
            raise ValueError("MATCHBOT_SES_SENDER is required when MATCHBOT_NOTIFIER=ses")
        if not settings.ses_recipients:
            raise ValueError("MATCHBOT_SES_RECIPIENTS is required when MATCHBOT_NOTIFIER=ses")
        recipients = [r.strip() for r in settings.ses_recipients.split(",") if r.strip()]
        return SESNotifier(settings.ses_sender, recipients, region=settings.aws_region)
    raise ValueError(f"Unknown notifier {settings.notifier!r}. Choose one of: log, ses.")
