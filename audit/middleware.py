import threading

_state = threading.local()


def get_current_user():
    return getattr(_state, 'user', None)


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
