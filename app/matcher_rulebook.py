from app.searching import matcher_rulebook as _impl
import sys as _sys

_sys.modules[__name__] = _impl
