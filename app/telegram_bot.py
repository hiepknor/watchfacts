from app.runtime import telegram_bot as _impl
import sys as _sys

_sys.modules[__name__] = _impl
