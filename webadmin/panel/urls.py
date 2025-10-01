from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("unresolved/", views.unresolved_users, name="unresolved_users"),
    path("status/", views.self_service, name="self_service"),
    path("broadcast/", views.broadcast, name="broadcast"),
    path("import-subscribers/", views.import_subscribers, name="import_subscribers"),
]

