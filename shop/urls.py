from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CartView, AddToCartView, RemoveCartItemView, ApplyDiscountView, RemoveDiscountView,
    OrderCheckoutView, OrderPaymentInitiateView, PaymentCallbackView,
    OrderHistoryViewSet
)

app_name = 'shop'

router = DefaultRouter()
router.register(r'orders/history', OrderHistoryViewSet, basename='order-history')

urlpatterns = [
    path('cart/', CartView.as_view(), name='cart-detail'),
    path('cart/items/', AddToCartView.as_view(), name='cart-add-item'),
    path('cart/items/<int:cart_item_pk>/remove/', RemoveCartItemView.as_view(), name='cart-remove-item'),
    path('cart/apply-discount/', ApplyDiscountView.as_view(), name='cart-apply-discount'),
    path('cart/remove-discount/', RemoveDiscountView.as_view(), name='cart-remove-discount'),

    path('orders/checkout/', OrderCheckoutView.as_view(), name='order-checkout'),
    path('orders/<int:order_pk>/initiate-payment/', OrderPaymentInitiateView.as_view(), name='order-initiate-payment'),

    path('payment/callback/', PaymentCallbackView.as_view(), name='payment_callback'),

    path('', include(router.urls)),
]
