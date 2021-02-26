import logging
import re

log = logging.getLogger(__name__)


def sanitize_title(title):
    """Returns title without any special characters"""
    alphanum_pattern = re.compile(r"[^\s\w]+")

    sanitized_title = alphanum_pattern.sub("", title).lower()
    if not sanitized_title:
        log.warning(f"Sanitizing {title} yielded an empty string")
        sanitized_title = " "

    return sanitized_title
