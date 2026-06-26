"""Completion notifiers.

On run completion the orchestrator notifies via the configured notifier with the
run summary (counts, match rate, DQ). ``LogNotifier`` (default) emits a
structured log line; ``SESNotifier`` emails via Amazon SES (``[aws]`` extra).
Both implement the same tiny interface, so adding Slack/SNS/etc. is one class.
"""

from matchbot.notify.base import LogNotifier, Notifier

__all__ = ["LogNotifier", "Notifier"]
