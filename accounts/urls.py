from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('notifications/read/', views.mark_notifications_read, name='mark_notifications_read'),
]
