from django.urls import path

from .views import (
    PaymentAttemptStartView,
    PaymentCallbackView,
    PaymentIntentCreateView,
    PaymentIntentStatusView,
)

app_name = "payments"

urlpatterns = [
    path("intents/", PaymentIntentCreateView.as_view(), name="intent-create"),
    path("intents/<uuid:intent_id>/", PaymentIntentStatusView.as_view(), name="intent-status"),
    path("intents/<uuid:intent_id>/attempts/", PaymentAttemptStartView.as_view(), name="attempt-start"),
    path("callbacks/<str:provider>/", PaymentCallbackView.as_view(), name="callback"),
]
