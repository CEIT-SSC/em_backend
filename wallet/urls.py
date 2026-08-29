from django.urls import path, include
from rest_framework.routers import DefaultRouter

from wallet.views import (
    WalletAdminAdjustView,
    WalletAdminRefundView,
    WalletBalanceView,
    WalletPayOrderView,
    WalletTopUpCallbackView,
    WalletTopUpStartView,
    WalletTopUpStatusView,
    WalletTransactionViewSet,
)

app_name = 'wallet'

router = DefaultRouter()
router.register(r'transactions', WalletTransactionViewSet, basename='wallet-transactions')

urlpatterns = [
    path('', WalletBalanceView.as_view(), name='balance'),
    path('top-ups/', WalletTopUpStartView.as_view(), name='topup-start'),
    path('top-ups/callback/', WalletTopUpCallbackView.as_view(), name='topup-callback'),
    path('top-ups/<uuid:public_id>/', WalletTopUpStatusView.as_view(), name='topup-status'),
    path('pay/', WalletPayOrderView.as_view(), name='pay-order'),
    path('admin/adjustments/', WalletAdminAdjustView.as_view(), name='admin-adjust'),
    path('admin/refunds/', WalletAdminRefundView.as_view(), name='admin-refund'),
    path('', include(router.urls)),
]
