"""Import every task module, so the worker's registry cannot drift.

A task module that is not imported here is a task the worker silently discards:
the producer enqueues, Celery logs "unregistered task ... ignored", and nothing
else happens — no retry, no error surfaced to anyone. That is precisely how the
mail queue was lost for a while (mail.py existed, its tests passed in eager
mode, and no worker had ever loaded it), so the list is discovered rather than
maintained by hand.
"""

import importlib
import pkgutil

for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module.name}")
