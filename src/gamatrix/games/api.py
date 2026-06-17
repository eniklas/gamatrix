"""JSON serialization for the comparison application contract."""

from __future__ import annotations

from gamatrix.games.service import ComparisonDataset, ComparisonQuery


def serialize_comparison(
    query: ComparisonQuery,
    dataset: ComparisonDataset,
) -> dict:
    """Serialize a comparison query and dataset for headless consumers."""
    return {
        "query": {
            "selected_user_ids": list(query.selected_user_ids),
            "include_single_player": query.include_single_player,
            "installed_only": query.installed_only,
            "exclude_platforms": list(query.exclude_platforms),
            "exclusive": query.exclusive,
            "scope": query.scope,
            "sort": {
                "field": query.sort.field,
                "direction": query.sort.direction,
            },
        },
        "total": dataset.total,
        "excluded_user_ids": list(dataset.excluded_user_ids),
        "games": [item.to_dict() for item in dataset.items],
    }
