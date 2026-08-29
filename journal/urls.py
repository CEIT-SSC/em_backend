from django.urls import path
from . import views

urlpatterns = [
    path('', views.journal_home, name='journal_home'),
    path('about/', views.journal_about, name='journal_about'),
    path('submit/<int:call_id>/', views.journal_submit, name='journal_submit'),
    path('release/<int:release_id>/', views.journal_release_detail, name='journal_release_detail'),
]