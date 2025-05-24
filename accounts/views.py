from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from rest_framework import generics, status, views, serializers
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, inline_serializer
from drf_spectacular.types import OpenApiTypes

SimpleMessageResponse = inline_serializer(name='SimpleMessageResponse', fields={'message': serializers.CharField()})
SimpleErrorResponse = inline_serializer(name='SimpleErrorResponse', fields={'error': serializers.CharField()})

from .serializers import (
    UserRegistrationSerializer,
    EmailVerificationSerializer,
    ResendVerificationEmailSerializer,
    UserProfileSerializer,
    UserProfileUpdateSerializer,
    ChangePasswordSerializer,
    SimpleForgotPasswordSerializer,
)
from .email_utils import send_email_async_task
from .utils import generate_numeric_code

CustomUser = get_user_model()


@extend_schema(
    summary="Register a new user",
    description="Creates a new user account if email doesn't exist. If email exists and is inactive, resends verification. If active, prompts to login.",
    request=UserRegistrationSerializer,
    responses={
        201: inline_serializer(name='UserRegistrationSuccess',
                               fields={'email': serializers.EmailField(), 'message': serializers.CharField()}),
        200: SimpleMessageResponse,  # For existing inactive user, verification resent
        400: OpenApiTypes.OBJECT,  # For validation errors or existing active user
    },
    tags=['Authentication']
)
class UserRegistrationView(generics.CreateAPIView):
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        # This method is called when a new user is actually being created
        user = serializer.save()
        code = generate_numeric_code(length=6)
        user.email_verification_code = code
        user.email_verification_code_expires_at = timezone.now() + timedelta(minutes=10)
        user.save()

        subject = 'Verify your email address for Event Manager'
        message = f'Hi {user.first_name or user.email},\n\nYour email verification code is: {code}\n\nThis code will expire in 10 minutes.\n\nThanks!'
        send_email_async_task(subject, message, [user.email])

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
                # User exists but is inactive, resend verification email
                code = generate_numeric_code(length=6)
                user.email_verification_code = code
                user.email_verification_code_expires_at = timezone.now() + timedelta(minutes=10)
                user.save()

                subject = 'Verify your email address for Event Manager (Account Exists)'
                message = f'Hi {user.first_name or user.email},\n\nAn attempt was made to register with your email. Your account is not yet active.\nYour new email verification code is: {code}\n\nThis code will expire in 10 minutes.\n\nThanks!'
                send_email_async_task(subject, message, [user.email])

                return Response(
                    {
                        "message": "An account with this email already exists but is not verified. A new verification code has been sent."},
                    status=status.HTTP_200_OK  # OK status as we took an action
                )
        except CustomUser.DoesNotExist:
            # Email does not exist, proceed with new user registration
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


# --- Other views remain the same as in the previous Canvas `django_accounts_views_final` ---
# For brevity, I'm only showing the modified UserRegistrationView.
# The rest of the views (EmailVerificationView, ResendVerificationEmailView, UserProfileView,
# ChangePasswordView, SimpleForgotPasswordView, TokenObtainPairView, TokenRefreshView, TokenVerifyView)
# would be the same as in the previous version of this file.

@extend_schema(
    summary="Verify user email",
    description="Activates a user account using the verification code sent to their email.",
    request=EmailVerificationSerializer,
    responses={
        200: SimpleMessageResponse,
        400: SimpleErrorResponse,
        404: SimpleErrorResponse,
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
        200: SimpleMessageResponse,
        400: SimpleMessageResponse,
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
                    return Response({"message": "This account is already active."}, status=status.HTTP_400_BAD_REQUEST)

                code = generate_numeric_code(length=6)
                user.email_verification_code = code
                user.email_verification_code_expires_at = timezone.now() + timedelta(minutes=10)
                user.save()

                subject = 'Resent: Verify your email address for Event Manager'
                message = f'Hi {user.first_name or user.email},\n\nYour new email verification code is: {code}\n\nThis code will expire in 10 minutes.\n\nThanks!'
                send_email_async_task(subject, message, [user.email])

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
    responses={200: SimpleMessageResponse},
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
        serializer.save()
        return Response({"message": "Password updated successfully"}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Simple forgot password",
    description="Resets user password to a new 8-digit code and emails it. User should change this temporary password upon login.",
    request=SimpleForgotPasswordSerializer,
    responses={200: SimpleMessageResponse},
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

                subject = 'Your Event Manager Password Has Been Reset'
                message = f'Hi {user.first_name or user.email},\n\nYour password has been reset. Your new temporary password is: {new_password}\n\nPlease log in using this password and change it immediately to something secure.\n\nThanks!'
                send_email_async_task(subject, message, [user.email])

                return Response({
                                    "message": "If an account with this email exists, a new temporary password has been sent to your email address. Please change it after logging in."},
                                status=status.HTTP_200_OK)
            except CustomUser.DoesNotExist:
                return Response({
                                    "message": "If an account with this email exists, a new temporary password has been sent to your email address. Please change it after logging in."},
                                status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
