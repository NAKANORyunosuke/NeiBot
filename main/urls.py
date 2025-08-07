from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path("twitch/callback/", views.twitch_callback, name="twitch_callback"),
]
