"""Deployment default for aiohttp clients in the AstrBot container.

Python imports this module from PYTHONPATH at startup. Keep explicit plugin
choices (trust_env=False or a proxy argument) authoritative.
"""

import functools
import os


def _enable_environment_proxy_default():
    if os.environ.get("XIAOXIAOQING_AIOHTTP_TRUST_ENV") != "1":
        return

    import aiohttp

    original = aiohttp.ClientSession.__init__
    if getattr(original, "_xiaoxiaoqing_environment_proxy_default", False):
        return

    @functools.wraps(original)
    def initialize_session(self, *args, **kwargs):
        kwargs.setdefault("trust_env", True)
        original(self, *args, **kwargs)

    initialize_session._xiaoxiaoqing_environment_proxy_default = True
    aiohttp.ClientSession.__init__ = initialize_session


_enable_environment_proxy_default()
