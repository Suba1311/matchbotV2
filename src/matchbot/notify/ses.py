"""Amazon SES notifier (optional, ``[aws]`` extra).

Mirrors the architecture's SES completion email: counts, match rate, DQ metrics.
boto3 is imported lazily so the core never depends on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matchbot.notify.base import Notifier

if TYPE_CHECKING:
    from matchbot.audit.metrics import RunMetrics


class SESNotifier(Notifier):
    """Email a run summary via Amazon SES."""

    def __init__(self, sender: str, recipients: list[str], region: str | None = None) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "SESNotifier requires boto3. Install with: pip install 'matchbot[aws]'"
            ) from exc
        self._ses = boto3.client("ses", region_name=region)
        self._sender = sender
        self._recipients = recipients

    def notify(self, metrics: RunMetrics) -> None:
        m = metrics.to_dict()
        subject = (
            f"MatchBot {m['status']}: {m['provider_id']} "
            f"({m['rows_matched']}/{m['rows_staged']} matched, "
            f"{m['match_rate']:.1%})"
        )
        body = "\n".join(f"{k}: {v}" for k, v in m.items() if k != "stage_timings")
        self._ses.send_email(
            Source=self._sender,
            Destination={"ToAddresses": self._recipients},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
