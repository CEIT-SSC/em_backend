from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CartView, CartItemView, ApplyDiscountView, RemoveDiscountView,
    OrderCheckoutView, OrderPaymentInitiateView, PaymentCallbackView,
    OrderHistoryViewSet, OrderCancelView, UserPurchasesView, ProductListView, CartPaymentInitiateView,
    TeamPaymentInitiateView
)

app_name = 'shop'

router = DefaultRouter()
router.register(r'orders/history', OrderHistoryViewSet, basename='order-history')

urlpatterns = [
    path('cart/', CartView.as_view(), name='cart-detail'),
    path('cart/items/', CartItemView.as_view(), name='cart-item-manage'),
    path('cart/apply-discount/', ApplyDiscountView.as_view(), name='cart-apply-discount'),
    path('cart/remove-discount/', RemoveDiscountView.as_view(), name='cart-remove-discount'),

    path('orders/checkout/', OrderCheckoutView.as_view(), name='order-checkout'),
    path('orders/<uuid:order_id>/initiate-payment/', OrderPaymentInitiateView.as_view(), name='order-initiate-payment'),
    path('orders/initiate-from-cart/', CartPaymentInitiateView.as_view(), name='initiate-from-cart'),
    path("orders/<uuid:order_id>/cancel/", OrderCancelView.as_view(), name="order-cancel-by-pk"),
    path('teams/<int:team_id>/initiate-payment/', TeamPaymentInitiateView.as_view(), name='team-initiate-payment'),
    path('payment/callback/', PaymentCallbackView.as_view(), name='payment_callback'),

    path('purchases/', UserPurchasesView.as_view(), name='user-purchases'),
    path('products/', ProductListView.as_view(), name='product-list'),

    path('', include(router.urls)),
]