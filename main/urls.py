from django.urls import path
from .views import redirect_to_login
from .views import home

urlpatterns = [
    path('', redirect_to_login, name='login'),
    path('', home, name='home'),
]
