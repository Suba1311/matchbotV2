"""Runtime-adapter boundary — the seam that makes the pipeline portable.

The pure pipeline core never imports boto3 / Glue / Snowflake. Everything
platform-specific lives behind two small interfaces:

* :class:`~matchbot.runtime.base.FileSystem` — read/list/write by URI
  (``file://`` locally, ``s3://`` on Fargate/Glue, stage on Snowflake).
* :class:`~matchbot.runtime.base.Runtime` — assembles the filesystem +
  repository and parses platform invocation args.

``get_runtime(name)`` returns the adapter selected by ``MATCHBOT_RUNTIME``.
Local is fully implemented; Fargate is implemented (S3); Glue and Snowflake are
stubs that raise a clear NotImplementedError describing what to fill in.
"""

from matchbot.runtime.base import FileSystem, Runtime
from matchbot.runtime.factory import get_runtime

__all__ = ["FileSystem", "Runtime", "get_runtime"]
