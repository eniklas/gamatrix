"""Web adapter and presenter for the games comparison UX.

This layer translates FastAPI request and preference semantics into the
application-layer comparison query, then shapes typed comparison results back
into the current Jinja template contract. The existing web UX stays intact
without forcing the comparison service to depend on Jinja/HTMX concerns.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from fastapi import Request

from gamatrix.games.preferences import merge_preferences
from gamatrix.games.service import (
    ComparisonDataset,
    ComparisonQuery,
    ComparisonRepository,
    SortSpec,
)


@dataclass
class WebCompareOptions:
    selected_user_ids: list[str] = field(default_factory=list)
    include_single_player: bool = False
    installed_only: bool = False
    exclude_platforms: list[str] = field(default_factory=list)
    exclusive: bool = False
    view: Literal["list", "grid"] = "list"
    randomize: bool = False
    show_keys: bool = False
    sort: str = "title"
    direction: Literal["asc", "desc"] = "asc"

    @property
    def all_games(self) -> bool:
        return self.view == "grid"

    def to_query(self) -> ComparisonQuery:
        return ComparisonQuery(
            selected_user_ids=list(self.selected_user_ids),
            include_single_player=self.include_single_player,
            installed_only=self.installed_only,
            exclude_platforms=list(self.exclude_platforms),
            exclusive=self.exclusive,
            scope="owned" if self.all_games else "shared",
            sort=SortSpec(field=self.sort, direction=self.direction),
        )


def parse_options(
    request: Request,
    user: dict,
    repo: ComparisonRepository,
) -> WebCompareOptions:
    """Build web options from saved preferences overlaid with query params."""
    prefs = merge_preferences(user.get("preferences", {}))
    qp = request.query_params

    # When the filter form is submitted it includes a hidden `filters_active`
    # marker. An off checkbox — or a list filter with everything deselected —
    # sends no value, so without this marker we can't tell "user cleared it"
    # from "not specified". When the form was submitted, an absent value means
    # empty/off; on a bare page load, fall back to the saved preference.
    form_submitted = "filters_active" in qp

    def flag(name: str, default: bool) -> bool:
        if name in qp:
            return qp[name] in ("true", "on", "1")
        return False if form_submitted else default

    def multi(name: str, default: list[str]) -> list[str]:
        values = qp.getlist(name)
        if values or form_submitted:
            return values
        return default

    # User selection: checked `user` boxes win. On a bare load fall back to the
    # saved preference, expanding the "all" sentinel to every known user.
    selected: list[str] = qp.getlist("user")
    if not selected and not form_submitted:
        pref_users = prefs["selected_users"]
        if pref_users == "all":
            selected = [
                str(u["user_id"]) for u in repo.scan_users() if u.get("user_id")
            ]
        else:
            selected = list(pref_users)

    view = qp.get("view", prefs["default_view"])
    if view not in ("list", "grid"):
        view = "list"
    direction: Literal["asc", "desc"] = (
        "desc" if qp.get("dir", "asc") == "desc" else "asc"
    )

    return WebCompareOptions(
        selected_user_ids=selected,
        include_single_player=flag("single_player", prefs["include_single_player"]),
        installed_only=flag("installed_only", prefs["installed_only"]),
        exclude_platforms=multi("exclude", prefs["exclude_platforms"]),
        exclusive=flag("exclusive", prefs["exclusive"]),
        view=view,
        randomize=flag("randomize", False),
        show_keys=flag("show_keys", prefs["show_keys"]),
        sort=qp.get("sort", "title"),
        direction=direction,
    )


def present_games(dataset: ComparisonDataset, opts: WebCompareOptions) -> list[dict]:
    """Convert typed comparison items into the current template payload."""
    games = [item.to_dict() for item in dataset.items]
    if opts.randomize and games:
        return [random.choice(games)]
    return games


def build_caption(
    users: dict[str, dict],
    opts: WebCompareOptions,
    dataset: ComparisonDataset,
) -> str:
    """Build the current English caption for the web UX."""
    names = [users[u]["username"] for u in opts.selected_user_ids if u in users]

    if opts.randomize:
        start = f"Random game selected from {dataset.total}"
    else:
        start = str(dataset.total)

    if opts.all_games:
        middle = "total games owned by"
    elif len(opts.selected_user_ids) == 1:
        middle = "games owned by"
    else:
        middle = "games in common between"

    caption = f"{start} {middle} {', '.join(names)}"

    if opts.exclusive and dataset.excluded_user_ids and not opts.all_games:
        excluded_names = [
            users[u]["username"] for u in dataset.excluded_user_ids if u in users
        ]
        caption += f" and not owned by {', '.join(excluded_names)}"
    if opts.exclude_platforms:
        caption += f" ({', '.join(opts.exclude_platforms).title()} excluded)"
    if opts.installed_only and not opts.all_games:
        caption += " (installed only)"
    return caption
