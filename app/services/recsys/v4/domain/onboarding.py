from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


def onboarding_feature_signature(
    *,
    genre_ids: Iterable[int],
    favorite_movie_ids: Iterable[int],
) -> str:
    payload = {
        "favorite_movie_ids": sorted(set(int(value) for value in favorite_movie_ids)),
        "genre_ids": sorted(set(int(value) for value in genre_ids)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
