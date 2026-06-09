"""Template-level guards for the shared base layout."""

from __future__ import annotations

import re

from gamatrix.templating import templates


def test_stylesheet_link_is_cache_busted():
    """The stylesheet must carry a version query so a deploy's CSS changes
    aren't masked by a browser serving a stale /static/style.css."""
    html = templates.env.get_template("base.html.jinja").render()
    assert re.search(r'href="/static/style\.css\?v=[^"]+"', html)
