from app.searching import search_contracts as _impl
import sys as _sys

_sys.modules[__name__] = _impl
