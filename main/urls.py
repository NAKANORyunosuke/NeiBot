from django.urls import path
from .views import home
from . import views

urlpatterns = [
    path('', home, name='home'),
    path("twitch/callback/", views.twitch_callback, name="twitch_callback"),
]
