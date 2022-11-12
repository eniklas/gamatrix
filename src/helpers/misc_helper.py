import logging
import re

log = logging.getLogger(__name__)


def get_slug_from_title(title):
    """Returns a URL-safe version of title. This is used to match the
    IGDB slug if more accurate methods fail, and to match the title
    better when dealing with slightly different titles across platforms
    """
    slug = "-"

    if not isinstance(title, str):
        log.warning(f'{title} is type {type(title)}, not string; using slug "{slug}"')
    else:
        # The IGDB slug algorithm is not published, but this is pretty close
        # A little less than half the time, an apostrophe is replaced with a dash,
        # so we'll miss those
        slug = title.replace("/", " slash ")
        # Remove special characters and replace whitespace with dashes
        slug = re.sub(r"\s+", "-", slug)
        slug = re.sub(r"[^0-9A-Za-z-]", "", slug).lower()
        # Collapse dashes for titles like "Dragon Age - Definitive Edition"
        slug = re.sub(r"[-]+", "-", slug)

        if not slug:
            slug = "-"
            log.warning(
                f'Converting {title} to slug yielded an empty string, using "{slug}"'
            )

    return slug
