"""Amazon SES notifier (optional, ``[aws]`` extra).

Emails an HTML run-summary table: status, provider, row counts, match rate,
duration, and which attributes the matcher chain actually compared on.
boto3 is imported lazily so the core never depends on it.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from matchbot.notify.base import Notifier

if TYPE_CHECKING:
    from matchbot.audit.metrics import RunMetrics


def _row(label: str, value: str) -> str:
    return (
        "<tr>"
        f'<td style="padding:6px 12px;border:1px solid #ddd;font-weight:bold;">{escape(label)}</td>'
        f'<td style="padding:6px 12px;border:1px solid #ddd;">{escape(value)}</td>'
        "</tr>"
    )


def _build_html(metrics: RunMetrics) -> str:
    m = metrics.to_dict()
    matched_on = ", ".join(metrics.matched_on) if metrics.matched_on else "—"
    rows = [
        _row("Status", m["status"].upper()),
        _row("Provider", m["provider_id"]),
        _row("Source file", m["source_uri"]),
        _row("Matched on", matched_on),
        _row("Rows staged", str(m["rows_staged"])),
        _row("Rows matched", str(m["rows_matched"])),
        _row("Rows unmatched", str(m["rows_unmatched"])),
        _row("Match rate", f"{m['match_rate']:.1%}"),
        _row("Duration (s)", str(m["duration_seconds"])),
    ]
    if m["error"]:
        rows.append(_row("Error", m["error"]))

    return f"""\
<html>
  <body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;">
    <h2 style="margin-bottom:4px;">MatchBot run summary</h2>
    <table style="border-collapse:collapse;">
      {"".join(rows)}
    </table>
  </body>
</html>"""


def _build_text(metrics: RunMetrics) -> str:
    """Plain-text fallback for clients that don't render HTML."""
    m = metrics.to_dict()
    matched_on = ", ".join(metrics.matched_on) if metrics.matched_on else "-"
    lines = [
        f"Status: {m['status'].upper()}",
        f"Provider: {m['provider_id']}",
        f"Source file: {m['source_uri']}",
        f"Matched on: {matched_on}",
        f"Rows staged: {m['rows_staged']}",
        f"Rows matched: {m['rows_matched']}",
        f"Rows unmatched: {m['rows_unmatched']}",
        f"Match rate: {m['match_rate']:.1%}",
        f"Duration (s): {m['duration_seconds']}",
    ]
    if m["error"]:
        lines.append(f"Error: {m['error']}")
    return "\n".join(lines)


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
        self._ses.send_email(
            Source=self._sender,
            Destination={"ToAddresses": self._recipients},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Html": {"Data": _build_html(metrics)},
                    "Text": {"Data": _build_text(metrics)},
                },
            },
        )
