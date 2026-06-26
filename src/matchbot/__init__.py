"""MatchBot V2 — multi-provider member-matching ETL pipeline.

A headless, fully-orchestrated pipeline that ingests provider files, cleanses
and standardizes them, maps them to a canonical schema, and matches them
against an authoritative Member Universe. Config-driven (no hardcoded columns),
Polars-based, and portable across AWS Fargate, AWS Glue, and Snowflake via a
thin runtime-adapter boundary.
"""

__version__ = "0.1.0"
