from django.urls import path

from . import views

app_name = "imaging"

urlpatterns = [
    path("scans/", views.scan_catalog, name="scan_catalog"),
    path("studies/", views.study_list, name="study_list"),
    path("studies/new/", views.study_create, name="study_create"),
    path("studies/<int:study_id>/", views.study_detail, name="study_detail"),
    path("studies/<int:study_id>/report/", views.study_report_edit, name="study_report_edit"),
    path("studies/<int:study_id>/deliver/", views.study_mark_delivered, name="study_mark_delivered"),
    path("studies/<int:study_id>/print/", views.study_report, name="study_report"),
    path("studies/<int:study_id>/collect/", views.collect_payment, name="collect_payment"),
]
