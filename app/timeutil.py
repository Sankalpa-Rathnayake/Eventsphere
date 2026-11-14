import os
from datetime import datetime, timedelta

# Sri Lanka is UTC+5:30. The server runs in UTC, so we shift the clock.
TZ_OFFSET_HOURS = float(os.getenv("TZ_OFFSET_HOURS", "5.5"))


def now():
    """Current local time (naive datetime)."""
    return datetime.utcnow() + timedelta(hours=TZ_OFFSET_HOURS)