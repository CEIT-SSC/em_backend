from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView
from django.core.exceptions import ObjectDoesNotExist
from oauth2_provider.views import TokenView
from rest_framework import generics, status, views
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema
from rest_framework.viewsets import ReadOnlyModelViewSet
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer
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
    SocialLoginSerializer, TokenRequestSerializer,
)
from .email_utils import send_email_async_task
from .utils import generate_numeric_code
from django.template.loader import render_to_string
from rest_framework.response import Response
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from oauth2_provider.models import get_application_model, AccessToken, RefreshToken

Application = get_application_model()


def generate_tokens_for_user(user, client_id):
    try:
        app = Application.objects.get(client_id=client_id)
    except ObjectDoesNotExist:
        raise ValueError("Invalid client_id provided.")

    AccessToken.objects.filter(user=user, application=app).delete()
    RefreshToken.objects.filter(user=user, application=app).delete()
    token_expires = timezone.now() + timedelta(seconds=settings.OAUTH2_PROVIDER['ACCESS_TOKEN_EXPIRE_SECONDS'])

    access_token = AccessToken.objects.create(
        user=user,
        application=app,
        expires=token_expires,
        scope="read write",
    )

    refresh_token = RefreshToken.objects.create(
        user=user,
        access_token=access_token,
        application=app,
    )

    return {
        "access_token": access_token.token,
        "expires_in": settings.OAUTH2_PROVIDER['ACCESS_TOKEN_EXPIRE_SECONDS'],
        "token_type": "Bearer",
        "scope": access_token.scope,
        "refresh_token": refresh_token.token,
    }


@extend_schema(
    summary="Register or Login with Google",
    description="""
        Handles user authentication via Google. The frontend should perform the OAuth2 flow
        and send the `code` (from the Authorization Code Flow with PKCE) to this endpoint.
    """,
    request=SocialLoginSerializer,
    responses={
        200: get_api_response_serializer(TokenSerializer),
        400: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class GoogleLoginView(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    client_class = OAuth2Client
    serializer_class = SocialLoginSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code != 200:
            return response

        client_id = request.data.get('client_id')
        user = self.user

        try:
            tokens = generate_tokens_for_user(user, client_id)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(tokens, status=status.HTTP_200_OK)

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
    request=TokenRequestSerializer,
    responses={
        200: TokenSerializer,
        400: ApiErrorResponseSerializer,
        401: ApiErrorResponseSerializer,
    },
    tags=['Authentication']
)
class CustomTokenView(TokenView):
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

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
            'expiration': expiry.strftime('%Y/%m/%d %H:%M'),
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
                user.email_verification_code_expires_at = timezone.now() + timedelta(minutes=10)
                user.save()

                expiry = user.email_verification_code_expires_at
                ctx = {
                    'code': code,
                    'expiration': expiry.strftime('%Y/%m/%d %H:%M'),
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
                user.email_verification_code_expires_at = timezone.now() + timedelta(minutes=10)
                user.save()

                expiry = user.email_verification_code_expires_at
                ctx = {
                    'code': code,
                    'expiration': expiry.strftime('%Y/%m/%d %H:%M'),
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
