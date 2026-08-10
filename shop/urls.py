from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import (
    ApplyDiscountView,
    CartItemView,
    CartView,
    OrderCancelView,
    OrderCheckoutView,
    OrderHistoryViewSet,
    ProductListView,
    RemoveDiscountView,
    UserPurchasesView,
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
    path('orders/<uuid:order_id>/cancel/', OrderCancelView.as_view(), name='order-cancel-by-pk'),

    path('purchases/', UserPurchasesView.as_view(), name='user-purchases'),
    path('products/', ProductListView.as_view(), name='product-list'),

    path('', include(router.urls)),
]