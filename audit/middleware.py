import contextlib
import threading

_state = threading.local()


def get_current_user():
    return getattr(_state, 'user', None)


@contextlib.contextmanager
def suppress_audit():
    """Detach the current user for a block so the signal-based audit logger skips
    it (`_actor()` returns None → `_log_save`/`_log_delete` bail out early).

    Used when a whole tenant is being deleted: the cascade would otherwise fire a
    DELETE log for every tracked row, each one referencing the very hospital being
    removed — rows that `AuditLog.hospital`'s own CASCADE deletes again in the same
    breath, and which crash on save as the hospital vanishes mid-cascade. Restores
    the previous user on exit, so it is safe to nest inside a request."""
    prev = getattr(_state, 'user', None)
    _state.user = None
    try:
        yield
    finally:
        _state.user = prev


class CurrentUserMiddleware:
    """Stash the request user in a thread-local so model signals can attribute
    changes to whoever made the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _state.user = getattr(request, 'user', None)
        try:
            return self.get_response(request)
        finally:
            _state.user = None
