import logging
import re

log = logging.getLogger(__name__)


def get_slug_from_title(title):
    """Returns a URL-safe version of title. This is used to match the
    IGDB slug if more accurate methods fail, and to match the title
    better when dealing with slightly different titles across platforms
    """
    alphanum_pattern = re.compile(r"[^\s\w]+")
    slug = "-"

    if not isinstance(title, str):
        log.warning(f'{title} is type {type(title)}, not string; using slug "{slug}"')
    else:
        # The IGDB slug algorithm is not published, but this is pretty close
        # Not always, but usually, an apostrophe is replaced with a dash
        slug = title.replace("'", " ").replace("/", " slash ")
        # Remove special characters and replace whitespace with dashes
        slug = alphanum_pattern.sub("", slug).lower()
        slug = re.sub(r"\s+", "-", slug)

        if not slug:
            slug = "-"
            log.warning(
                f'Converting {title} to slug yielded an empty string, using "{slug}"'
            )

    return slug
