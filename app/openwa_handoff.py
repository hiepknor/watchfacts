from app.integrations import openwa_handoff as _impl
import sys as _sys

_sys.modules[__name__] = _impl
