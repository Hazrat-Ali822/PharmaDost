"""One helper so every list paginates the same way.

Usage in a view::

    from pharma_mgmt.pagination import paginate
    page = paginate(request, queryset)          # a Django Page
    return render(request, "...", {"rows": page, "page_obj": page})

The template loops the page directly (a Page is iterable) and includes
``partials/_pagination.html``, which reads ``page_obj`` and preserves the search
and filter query params while paging.
"""
from django.core.paginator import Paginator

DEFAULT_PER_PAGE = 25


def paginate(request, queryset, per_page=DEFAULT_PER_PAGE):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))
