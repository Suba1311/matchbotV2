"""Completion notifiers.

On run completion the orchestrator notifies via the configured notifier with the
run summary (counts, match rate, DQ). ``LogNotifier`` (default) emits a
structured log line; ``SESNotifier`` emails via Amazon SES (``[aws]`` extra),
selected via ``MATCHBOT_NOTIFIER=ses``. Both implement the same tiny interface,
so adding Slack/SNS/etc. is one class + a branch in ``get_notifier``.
"""

from matchbot.notify.base import LogNotifier, Notifier
from matchbot.notify.factory import get_notifier

__all__ = ["LogNotifier", "Notifier", "get_notifier"]
