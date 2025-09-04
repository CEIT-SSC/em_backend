from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UserRegistrationView,
    EmailVerificationView,
    ResendVerificationEmailView,
    UserProfileView,
    ChangePasswordView,
    SimpleForgotPasswordView,
    GoogleLogin,
    StaffViewSet, CustomTokenObtainView
)
from rest_framework_simplejwt.views import TokenRefreshView, token_blacklist

app_name = 'accounts'

router = DefaultRouter()
router.register(r'staff', StaffViewSet, basename='staff')

urlpatterns = [
    path('register/', UserRegistrationView.as_view(), name='user_register'),
    path('verify-email/', EmailVerificationView.as_view(), name='email_verify'),
    path('resend-verify-email/', ResendVerificationEmailView.as_view(), name='resend_verify_email'),

    path('token/', CustomTokenObtainView.as_view(), name='token'),
    path('token/refresh/', TokenRefreshView.as_view(), name='refresh_token'),
    path('token/blacklist/', token_blacklist, name='blacklist_token'),

    path('', include(router.urls)),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('forgot-password/', SimpleForgotPasswordView.as_view(), name='forgot_password_simple'),

    path('auth/google/', GoogleLogin.as_view(), name='google_login'),
]
