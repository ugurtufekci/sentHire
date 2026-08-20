"""Offline stand-ins for every model call.

Enabled by `SENTHIRE_FAKE_MODELS=1`. The pipeline then runs end to end with no
API key, no tokens and no network: useful for local development, for CI smoke
tests against real servers, and for showing the product without spending money
on a demo.

It is deliberately loud rather than convenient. Every run started in this mode
is stamped `fake_models` and the UI says so, because a screening result nobody
can tell apart from a real one is a liability, not a feature.
"""
