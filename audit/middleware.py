import contextlib
import threading

_state = threading.local()


def get_current_user():
    return getattr(_state, 'user', None)


def get_current_ip():
    """Where the request came from, for the audit trail and the login lockout.

    A security log that cannot say *where from* only tells half the story, and
    the lockout keys on (email, IP) so that one attacker cannot lock a whole
    hospital's staff out of their own system.
    """
    return getattr(_state, 'ip', '') or ''


def _client_ip(request):
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return (forwarded or request.META.get('REMOTE_ADDR') or '')[:45]


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
        _state.ip = _client_ip(request)
        try:
            return self.get_response(request)
        finally:
            _state.user = None
            _state.ip = ''
