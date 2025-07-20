from dj_rest_auth.registration.serializers import SocialLoginSerializer
from dj_rest_auth.serializers import JWTSerializer
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from rest_framework import generics, status, views
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse
from drf_spectacular.types import OpenApiTypes
from em_backend import settings
from .serializers import (
    UserRegistrationSerializer,
    EmailVerificationSerializer,
    ResendVerificationEmailSerializer,
    UserProfileSerializer,
    UserProfileUpdateSerializer,
    ChangePasswordSerializer,
    SimpleForgotPasswordSerializer, MessageResponseSerializer, ErrorResponseSerializer,
    UserRegistrationSuccessSerializer,
)
from .email_utils import send_email_async_task
from .utils import generate_numeric_code
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView
from django.template.loader import render_to_string

CustomUser = get_user_model()

@extend_schema(
    summary="Register or Login with Google (PKCE or Implicit Flow)",
    description="""
        Handles user authentication via Google. This endpoint is flexible and accepts either:
            1.  Authorization Code Flow (PKCE): `code`
            2.  Implicit Flow: `access_token`
    """,
    request=SocialLoginSerializer,
    responses={
        200: OpenApiResponse(response=JWTSerializer, description="Successfully authenticated"),
        400: OpenApiResponse(description="Bad Request"),
    },
    tags=['Authentication']
)
class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    callback_url = settings.FRONTEND_URL
    client_class = OAuth2Client

@extend_schema(
    summary="Register a new user",
    description="Creates a new user account if email doesn't exist. If email exists and is inactive, resends verification. If active, prompts to login.",
    request=UserRegistrationSerializer,
    responses={
        201: UserRegistrationSuccessSerializer,
        200: MessageResponseSerializer,
        400: ErrorResponseSerializer,
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
        200: MessageResponseSerializer,
        400: ErrorResponseSerializer,
        404: ErrorResponseSerializer,
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
        200: MessageResponseSerializer,
        400: ErrorResponseSerializer,
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
    responses={200: UserProfileSerializer},
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
        200: MessageResponseSerializer,
        400: ErrorResponseSerializer,
    },
    tags=['User Profile']
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
        200: MessageResponseSerializer,
        400: OpenApiTypes.OBJECT,
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
