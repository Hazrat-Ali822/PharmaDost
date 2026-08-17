from django.urls import path

from .views import message_log

urlpatterns = [
    path('', message_log, name='message_log'),
]
