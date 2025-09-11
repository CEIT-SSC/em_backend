import json
from urllib.parse import urlencode
from django.contrib.auth import authenticate, login
from django.core import signing
from django.http import HttpResponseRedirect
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView
from oauth2_provider.models import get_application_model, RefreshToken
from oauth2_provider.settings import oauth2_settings
from oauth2_provider.views import (
    TokenView,
    RevokeTokenView,
    AuthorizationView
)
from rest_framework import generics, status, views
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema
from rest_framework.viewsets import ReadOnlyModelViewSet
from em_backend import settings
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer
from .adapters import ProxiedGoogleOAuth2Adapter
from .models import Staff, CustomUser
from .serializers import (
    UserRegistrationSerializer,
    EmailVerificationSerializer,
    ResendVerificationEmailSerializer,
    UserProfileSerializer,
    UserProfileUpdateSerializer,
    ChangePasswordSerializer,
    SimpleForgotPasswordSerializer,
    UserRegistrationSuccessSerializer,
    StaffSerializer,
    TokenSerializer,
    SocialLoginSerializer,
    TokenRequestSerializer,
    RevokeTokenRequestSerializer,
    HandshakeTokenSerializer,
    AuthorizationFormSerializer, RefreshTokenSerializer
)
from .email_utils import send_email_async_task
from .utils import generate_numeric_code
from django.template.loader import render_to_string
from rest_framework.response import Response
from datetime import timedelta
from django.core.signing import BadSignature, SignatureExpired
import pytz


def _format_datetime(dt):
    iran_tz = pytz.timezone('Asia/Tehran')
    local_dt = timezone.localtime(dt, iran_tz)
    return local_dt.strftime('%Y/%m/%d %H:%M')


@extend_schema(
    summary="Start Google SSO Flow",
    description="""
        The frontend sends a Google `code`. (Manual Google Authorization URL, NO GIS initCodeClient, NO PKCE)
        This endpoint validates it and returns a short-lived, single-use `handshake_token`.
        The frontend must then immediately redirect the user to the `/o/authorize/` endpoint, passing this token.
    """,
    request=SocialLoginSerializer,
    responses={
        200: get_api_response_serializer(HandshakeTokenSerializer),
        400: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class GoogleLoginView(SocialLoginView):
    permission_classes = [AllowAny]
    adapter_class = ProxiedGoogleOAuth2Adapter
    client_class = OAuth2Client
    serializer_class = SocialLoginSerializer
    callback_url = settings.GOOGLE_CALLBACK_URL

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        user = getattr(self, "user", None)

        if response.status_code == 200 and user:
            payload = {
                "user_pk": user.pk,
                "ts": int(timezone.now().timestamp())
            }
            handshake_token = signing.dumps(payload)
            return Response({"handshake_token": handshake_token}, status=status.HTTP_200_OK)

        try:
            resp_data = response.data
        except Exception:
            resp_data = {"detail": "Social login failed"}
        return Response(resp_data, status=response.status_code)


@extend_schema(
    summary="Obtain OAuth2 Tokens (Login/Refresh)",
    description="""
        This endpoint is used for all OAuth2 token-related operations.

        **1. Login with Email & Password:**
        ```json
        {
          "grant_type": "password",
          "username": "user@example.com",
          "password": "your-password",
          "client_id": "your-client-id"
        }
        ```

        **2. Refresh an Access Token:**
        ```json
        {
          "grant_type": "refresh_token",
          "refresh_token": "your-refresh-token",
          "client_id": "your-client-id"
        }
        ```
        **Note:** This endpoint expects a `Content-Type` of `application/x-www-form-urlencoded`.
    """,
    request={'application/x-www-form-urlencoded': TokenRequestSerializer},
    responses={
        200: get_api_response_serializer(TokenSerializer),
        400: ApiErrorResponseSerializer,
        401: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class CustomTokenView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        django_request = request._request
        django_request.POST = request.data
        django_request.META['CONTENT_TYPE'] = 'application/x-www-form-urlencoded'

        original_response = TokenView.as_view()(django_request, *args, **kwargs)

        try:
            data = json.loads(original_response.content)
        except json.JSONDecodeError:
            data = {}

        return Response(data, status=original_response.status_code)


@extend_schema(
    summary="Revoke OAuth2 Tokens (Logout)",
    description="""
        This endpoint revokes an access or refresh token, effectively logging a user out from a client.
        **Note:** This endpoint expects a `Content-Type` of `application/x-www-form-urlencoded`.
    """,
    request={'application/x-www-form-urlencoded': RevokeTokenRequestSerializer},
    responses={
        200: get_api_response_serializer(None),
        400: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class CustomRevokeTokenView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        django_request = request._request
        django_request.POST = request.data
        django_request.META['CONTENT_TYPE'] = 'application/x-www-form-urlencoded'

        original_response = RevokeTokenView.as_view()(django_request, *args, **kwargs)

        try:
            data = json.loads(original_response.content)
        except json.JSONDecodeError:
            data = {}

        return Response(data, status=original_response.status_code)


@extend_schema(tags=['Authentication'])
class CustomAuthorizationView(views.APIView):
    permission_classes = [AllowAny]
    authorization_view_class = AuthorizationView

    @extend_schema(
        summary="Authorization Checkpoint (Headless SSO)",
        description="""
            It requires a valid session cookie to have been established by a prior API call (like `/social/google/`).

            This endpoint validates the short-lived handshake_token and immediately redirects the user back to the
            original client's `redirect_uri` with an `authorization_code`.
        """,
        parameters=[
            OpenApiParameter(name='response_type', required=True, type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name='client_id', required=True, type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name='redirect_uri', required=True, type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name='handshake_token', required=False, type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name='scope', type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name='code_challenge', type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name='code_challenge_method', type=str, location=OpenApiParameter.QUERY),
        ],
        responses={
            302: "Redirects back to the client's `redirect_uri` with a `code` or an `error`.",
        },
        tags=['Authentication']
    )
    def get(self, request, *args, **kwargs):
        django_request = request._request
        handshake_token = django_request.GET.get('handshake_token')
        redirect_url = request.query_params.get('redirect_uri')

        if handshake_token:
            try:
                payload = signing.loads(handshake_token, max_age=120)
                user_pk = payload.get("user_pk")
                user = CustomUser.objects.get(pk=user_pk)

            except (SignatureExpired, BadSignature, CustomUser.DoesNotExist) as e:
                error_code = "unknown_error"

                if isinstance(e, SignatureExpired):
                    error_code = "handshake_expired"
                elif isinstance(e, BadSignature):
                    error_code = "invalid_handshake"
                elif isinstance(e, CustomUser.DoesNotExist):
                    error_code = "user_not_found"

                error_params = urlencode({'error': error_code,})
                return HttpResponseRedirect(f"{redirect_url}?{error_params}")

            django_request.user = user

        return self.authorization_view_class.as_view()(django_request, *args, **kwargs)

    @extend_schema(
        summary="Authorization Page (Form Submission)",
        description="This endpoint handles the form submission from the central login page.",
        request={'application/x-www-form-urlencoded': AuthorizationFormSerializer},
        responses={302: "Redirects back to the client's `redirect_uri` after successful login."},
        tags=['Authentication']
    )
    def post(self, request, *args, **kwargs):
        django_request = request._request
        django_request.POST = request.data

        client_id = request.data.get('client_id') or request.query_params.get('client_id')

        if not client_id:
            return Response(
                {"detail": "Missing required 'client_id' parameter."},
                status=status.HTTP_400_BAD_REQUEST
            )

        Application = get_application_model()
        try:
            Application.objects.get(client_id=client_id)
        except Application.DoesNotExist:
            return Response(
                {"detail": "Invalid client_id: application not found."},
                status=status.HTTP_400_BAD_REQUEST
            )

        username = request.data.get('username') or request.data.get('email')
        password = request.data.get('password')

        if username and password:
            user = authenticate(django_request, username=username, password=password)
            if user:
                user.backend = 'django.contrib.auth.backends.ModelBackend'
                login(django_request, user)
            else:
                return Response({"detail": "Invalid username or password, or this user is inactive."},
                                status=status.HTTP_401_UNAUTHORIZED)

        return self.authorization_view_class.as_view()(django_request, *args, **kwargs)


@extend_schema(
    summary="Authorize via Refresh Token",
    description="Exchanges a valid refresh_token for a short-lived handshake_token to complete an SSO flow.",
    request=RefreshTokenSerializer,
    responses={
        200: get_api_response_serializer(HandshakeTokenSerializer),
        400: ApiErrorResponseSerializer,
        401: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class AuthorizeWithTokenView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = RefreshTokenSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token_str = serializer.validated_data['refresh_token']
        try:
            rt = RefreshToken.objects.get(token=token_str)
            if rt.revoked:
                return Response({"error": "Refresh token has been revoked."}, status=status.HTTP_401_UNAUTHORIZED)

            expires_at = rt.created + timedelta(seconds=oauth2_settings.REFRESH_TOKEN_EXPIRE_SECONDS)
            if timezone.now() > expires_at:
                rt.revoke()
                return Response({"error": "Refresh token has expired."}, status=status.HTTP_401_UNAUTHORIZED)

            user = rt.user
            payload = {
                "user_pk": user.pk,
                "ts": int(timezone.now().timestamp())
            }
            handshake_token = signing.dumps(payload)
            return Response({"handshake_token": handshake_token}, status=status.HTTP_200_OK)

        except RefreshToken.DoesNotExist:
            return Response({"detail": "Refresh token is invalid."}, status=status.HTTP_401_UNAUTHORIZED)


@extend_schema(
    summary="Register a new user",
    description="Creates a new user account if email doesn't exist. If email exists and is inactive, resends verification. If active, prompts to login.",
    request=UserRegistrationSerializer,
    responses={
        201: get_api_response_serializer(UserRegistrationSuccessSerializer),
        200: get_api_response_serializer(None),
        400: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class UserRegistrationView(generics.CreateAPIView):
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        user = serializer.save()
        code = generate_numeric_code(length=6)
        expiry = timezone.now() + timedelta(minutes=10)
        user.email_verification_code = code
        user.email_verification_code_expires_at = expiry
        user.save()

        ctx = {
            'code': code,
            'expiration': _format_datetime(expiry),
        }
        html_content = render_to_string('verification.html', ctx)
        text_content = f'کد تأیید شما: {code}\nاین کد تا {ctx["expiration"]} معتبر است.'

        subject = 'تأیید ایمیل'
        send_email_async_task(
            subject=subject,
            recipient_list=[user.email],
            text_content=text_content,
            html_content=html_content
        )

    def create(self, request, *args, **kwargs):
        email = request.data.get('email')
        if not email:
            return Response({"email": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = CustomUser.objects.get(email__iexact=email)  # Case-insensitive check
            if user.is_active:
                return Response(
                    {
                        "error": "An active account with this email already exists. Please log in or use 'forgot password'."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            else:
                code = generate_numeric_code(length=6)
                user.email_verification_code = code
                user.email_verification_code_expires_at = timezone.now() + timedelta(minutes=2)
                user.save()

                expiry = user.email_verification_code_expires_at
                ctx = {
                    'code': code,
                    'expiration': _format_datetime(expiry),
                }
                html_content = render_to_string('verification.html', ctx)
                text_content = f'کد تأیید شما: {code}\nاین کد تا {ctx["expiration"]} معتبر است.'

                subject = 'تأیید ایمیل'
                send_email_async_task(
                    subject=subject,
                    recipient_list=[user.email],
                    text_content=text_content,
                    html_content=html_content
                )
                return Response(
                    {
                        "message": "An account with this email already exists but is not verified. A new verification code has been sent."},
                    status=status.HTTP_200_OK
                )

        except CustomUser.DoesNotExist:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            user_data = serializer.data
            response_data = {
                "email": user_data.get("email"),
                "message": "User registered successfully. Please check your email for verification code."
            }
            headers = self.get_success_headers(serializer.data)
            return Response(response_data, status=status.HTTP_201_CREATED, headers=headers)

@extend_schema(
    summary="Verify user email",
    description="Activates a user account using the verification code sent to their email.",
    request=EmailVerificationSerializer,
    responses={
        200: get_api_response_serializer(None),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class EmailVerificationView(views.APIView):
    permission_classes = [AllowAny]
    serializer_class = EmailVerificationSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            code = serializer.validated_data['code']
            try:
                user = CustomUser.objects.get(email=email)
                if user.is_active:
                    return Response({"error": "Email already verified."}, status=status.HTTP_400_BAD_REQUEST)

                if user.email_verification_code == code and \
                        user.email_verification_code_expires_at and \
                        user.email_verification_code_expires_at > timezone.now():

                    user.is_active = True
                    user.email_verification_code = None
                    user.email_verification_code_expires_at = None
                    user.save()
                    return Response({"message": "Email successfully verified. You can now log in."},
                                    status=status.HTTP_200_OK)
                else:
                    return Response({"error": "Invalid or expired verification code."},
                                    status=status.HTTP_400_BAD_REQUEST)
            except CustomUser.DoesNotExist:
                return Response({"error": "User with this email does not exist."}, status=status.HTTP_404_NOT_FOUND)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Resend email verification code",
    description="Resends the email verification code if the user's account is not yet active.",
    request=ResendVerificationEmailSerializer,
    responses={
        200: get_api_response_serializer(None),
        400: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class ResendVerificationEmailView(views.APIView):
    permission_classes = [AllowAny]
    serializer_class = ResendVerificationEmailSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            try:
                user = CustomUser.objects.get(email=email)
                if user.is_active:
                    return Response({"error": "This account is already active."}, status=status.HTTP_400_BAD_REQUEST)

                code = generate_numeric_code(length=6)
                user.email_verification_code = code
                user.email_verification_code_expires_at = timezone.now() + timedelta(minutes=2)
                user.save()

                expiry = user.email_verification_code_expires_at
                ctx = {
                    'code': code,
                    'expiration': _format_datetime(expiry),
                }
                html_content = render_to_string('verification.html', ctx)
                text_content = f'کد تأیید جدید شما: {code}\nاین کد تا {ctx["expiration"]} معتبر است.'

                subject = 'تأیید ایمیل'
                send_email_async_task(
                    subject=subject,
                    recipient_list=[user.email],
                    text_content=text_content,
                    html_content=html_content
                )
                return Response({"message": "A new verification email has been sent."}, status=status.HTTP_200_OK)

            except CustomUser.DoesNotExist:
                return Response({
                                    "message": "If an account with this email exists and is not active, a new verification email has been sent."},
                                status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Retrieve or update user profile",
    description="Allows authenticated users to retrieve or update their profile information.",
    responses={
        200: get_api_response_serializer(UserProfileSerializer),
        400: ApiErrorResponseSerializer,
    },
    tags=['User Profile']
)
class UserProfileView(generics.RetrieveUpdateAPIView):
    queryset = CustomUser.objects.all()
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return UserProfileUpdateSerializer
        return UserProfileSerializer


@extend_schema(
    summary="Change user password",
    description="Allows authenticated users to change their current password.",
    request=ChangePasswordSerializer,
    responses={
        200: get_api_response_serializer(None),
        400: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class ChangePasswordView(generics.UpdateAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self, queryset=None):
        return self.request.user

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        old_password = serializer.validated_data.get('old_password')
        new_password = serializer.validated_data.get('new_password')
        if old_password == new_password:
            return Response(
                {"error": "New password cannot be the same as the old password."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer.save()
        return Response({"message": "Password updated successfully"}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Simple forgot password",
    description="Resets user password to a new 8-digit code and emails it. User should change this temporary password upon login.",
    request=SimpleForgotPasswordSerializer,
    responses={
        200: get_api_response_serializer(None),
        400: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class SimpleForgotPasswordView(views.APIView):
    permission_classes = [AllowAny]
    serializer_class = SimpleForgotPasswordSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            try:
                user = CustomUser.objects.get(email=email)
                new_password = generate_numeric_code(length=8)
                user.set_password(new_password)
                user.save()

                ctx = {
                    'password': new_password,
                }
                html_content = render_to_string('reset_password.html', ctx)
                text_content = f'رمز عبور موقت شما: {new_password}\nلطفاً پس از ورود آن را تغییر دهید.'

                subject = 'رمز عبور جدید'
                send_email_async_task(
                    subject=subject,
                    recipient_list=[user.email],
                    text_content=text_content,
                    html_content=html_content
                )

                return Response({
                                    "message": "If an account with this email exists, a new temporary password has been sent to your email address. Please change it after logging in."},
                                status=status.HTTP_200_OK)
            except CustomUser.DoesNotExist:
                return Response({
                                    "message": "If an account with this email exists, a new temporary password has been sent to your email address. Please change it after logging in."},
                                status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="List or Retrieve Staff",
    request=None,
    responses={
        200: get_api_response_serializer(StaffSerializer),
        404: ApiErrorResponseSerializer,
    },
    tags=["Staff"]
)
class StaffViewSet(ReadOnlyModelViewSet):
    queryset = Staff.objects.all()
    serializer_class = StaffSerializer
