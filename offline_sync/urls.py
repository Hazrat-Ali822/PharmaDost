from django.urls import path

from . import views

urlpatterns = [
    path("sync/", views.sync, name="offline_sync"),
    # Reachability probe — the client checks this before every offline-capable
    # submit, because `navigator.onLine` only knows about the network link.
    path("ping/", views.ping, name="offline_ping"),
    # The outbox screen (pending + rejected entries), readable with no connection.
    path("queue/", views.queue_page, name="offline_queue"),
    # The printable slip for a visit registered offline — the patient carries it to
    # the doctor, which is the handoff when no notification can travel.
    path("slip/", views.provisional_slip, name="offline_slip"),
]
