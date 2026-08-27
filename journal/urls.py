from django.urls import path
from . import views

urlpatterns = [
    path('', views.journal_home, name='journal_home'),
    path('submit/', views.journal_submit, name='journal_submit'),
]