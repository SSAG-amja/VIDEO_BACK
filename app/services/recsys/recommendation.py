"""Backward-compatible imports for the v1 rule-based recommender."""

from app.services.recsys.v1 import recommendation as _v1

globals().update({name: value for name, value in vars(_v1).items() if not name.startswith("__")})
