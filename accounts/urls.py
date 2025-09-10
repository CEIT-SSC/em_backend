from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UserRegistrationView,
    EmailVerificationView,
    ResendVerificationEmailView,
    UserProfileView,
    ChangePasswordView,
    SimpleForgotPasswordView,
    StaffViewSet,
    GoogleLoginView,
    CustomTokenView,
    CustomAuthorizationView,
    CustomRevokeTokenView, AuthorizeWithTokenView,
)

app_name = 'accounts'

router = DefaultRouter()
router.register(r'staff', StaffViewSet, basename='staff')

oauth2_urlpatterns = [
    path('authorize/', CustomAuthorizationView.as_view(), name='authorize'),
    path('authorize/refresh', AuthorizeWithTokenView.as_view(), name='authorize_with_token'),
    path('token/', CustomTokenView.as_view(), name='token'),
    path('revoke-token/', CustomRevokeTokenView.as_view(), name='revoke-token'),
]

urlpatterns = [
    path('o/', include(oauth2_urlpatterns)),
    path('social/google/', GoogleLoginView.as_view(), name='google_login'),

    path('register/', UserRegistrationView.as_view(), name='user_register'),
    path('verify-email/', EmailVerificationView.as_view(), name='email_verify'),
    path('resend-verify-email/', ResendVerificationEmailView.as_view(), name='resend_verify_email'),

    path('', include(router.urls)),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('forgot-password/', SimpleForgotPasswordView.as_view(), name='forgot_password_simple'),
]
