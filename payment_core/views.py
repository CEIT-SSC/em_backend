from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, views
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .exceptions import (
    AttemptNotFound,
    DuplicateGatewayIdentifier,
    IdempotencyConflict,
    InvalidStateTransition,
    PaymentError,
    PaymentNotFound,
    ProviderFailure,
)
from .serializers import (
    CallbackResponseSerializer,
    CreatePaymentIntentSerializer,
    PaymentAttemptSerializer,
    PaymentErrorSerializer,
    PaymentIntentSerializer,
    StartPaymentAttemptSerializer,
)
from .services import create_intent, get_payment_status, start_payment_attempt, verify_callback


def _error_response(exc):
    if isinstance(exc, (PaymentNotFound, AttemptNotFound)):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, (IdempotencyConflict, InvalidStateTransition, DuplicateGatewayIdentifier)):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, ProviderFailure):
        code = status.HTTP_502_BAD_GATEWAY
    else:
        code = status.HTTP_400_BAD_REQUEST
    return Response({"error": str(exc), "code": exc.code}, status=code)


class PaymentIntentCreateView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Payments"],
        summary="Create a payment intent",
        description=(
            "Creates what must be paid in integer Rials. Reusing an idempotency key with the same "
            "request returns the existing intent; changed data is rejected."
        ),
        request=CreatePaymentIntentSerializer,
        responses={201: PaymentIntentSerializer, 200: PaymentIntentSerializer, 409: PaymentErrorSerializer},
    )
    def post(self, request):
        serializer = CreatePaymentIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            intent, created = create_intent(user=request.user, **serializer.validated_data)
        except PaymentError as exc:
            return _error_response(exc)
        return Response(
            PaymentIntentSerializer(intent).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PaymentIntentStatusView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Payments"],
        summary="Get authoritative payment status",
        description="Returns payment-core state stored by the backend, including all provider attempts.",
        responses={200: PaymentIntentSerializer, 404: PaymentErrorSerializer},
    )
    def get(self, request, intent_id):
        try:
            intent = get_payment_status(intent_id=intent_id, user=request.user)
        except PaymentError as exc:
            return _error_response(exc)
        return Response(PaymentIntentSerializer(intent).data)


class PaymentAttemptStartView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Payments"],
        summary="Start a provider payment attempt",
        description="One intent may have multiple independently identified provider attempts.",
        request=StartPaymentAttemptSerializer,
        responses={
            201: PaymentAttemptSerializer, 200: PaymentAttemptSerializer,
            404: PaymentErrorSerializer, 409: PaymentErrorSerializer, 502: PaymentErrorSerializer,
        },
    )
    def post(self, request, intent_id):
        serializer = StartPaymentAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            intent = get_payment_status(intent_id=intent_id, user=request.user)
            attempt, created = start_payment_attempt(intent=intent, **serializer.validated_data)
        except PaymentError as exc:
            return _error_response(exc)
        return Response(
            PaymentAttemptSerializer(attempt).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PaymentCallbackView(views.APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def _handle(self, request, provider):
        authority = (
            request.query_params.get("Authority") or request.query_params.get("authority")
            or request.data.get("Authority") or request.data.get("authority")
        )
        if not authority:
            return Response(
                {"error": "A gateway authority is required.", "code": "invalid_callback"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            attempt, settlement = verify_callback(provider=provider, authority=authority)
        except PaymentError as exc:
            return _error_response(exc)
        return Response({
            "intent_id": attempt.intent_id,
            "attempt_id": attempt.id,
            "payment_status": attempt.intent.status,
            "attempt_status": attempt.status,
            "settlement_status": settlement.status if settlement else None,
        })

    @extend_schema(
        tags=["Payments"],
        summary="Verify a payment provider callback",
        description=(
            "The callback status supplied by the browser is ignored. The backend locates the stored "
            "attempt by authority and asks the selected provider adapter to verify it."
        ),
        parameters=[OpenApiParameter("Authority", str, required=True, location=OpenApiParameter.QUERY)],
        responses={200: CallbackResponseSerializer, 400: PaymentErrorSerializer, 404: PaymentErrorSerializer,
                   502: PaymentErrorSerializer},
    )
    def get(self, request, provider):
        return self._handle(request, provider)

    @extend_schema(
        tags=["Payments"],
        summary="Verify a payment provider webhook callback",
        description="Equivalent POST callback. Provider status fields are never accepted as proof of payment.",
        request=None,
        responses={200: CallbackResponseSerializer, 400: PaymentErrorSerializer, 404: PaymentErrorSerializer,
                   502: PaymentErrorSerializer},
    )
    def post(self, request, provider):
        return self._handle(request, provider)
