import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from rest_framework import status, views, viewsets
from rest_framework.response import Response

from em_backend.schemas import (
    ApiErrorResponseSerializer,
    get_api_response_serializer,
    get_paginated_response_serializer,
)
from shop.models import Order
from wallet.exceptions import (
    AdjustmentReasonRequired,
    DuplicateIdempotencyKey,
    InsufficientFunds,
    InvalidAmount,
    OrderNotPayable,
    RefundNotAllowed,
    TopUpGatewayError,
    TopUpNotFound,
    WalletError,
)
from wallet.models import WalletEntry
from wallet.pagination import WalletEntryPagination
from wallet.permissions import IsAuthenticatedUser, IsStaffUser
from wallet.serializers import (
    AdminAdjustmentSerializer,
    AdminLedgerResultSerializer,
    AdminRefundSerializer,
    PayOrderSerializer,
    StartTopUpSerializer,
    TopUpSerializer,
    TopUpStartResponseSerializer,
    WalletBalanceSerializer,
    WalletEntrySerializer,
    WalletPurchaseSerializer,
)
from wallet.services import WalletService

logger = logging.getLogger(__name__)
User = get_user_model()


def _error_status(exc):
    if isinstance(exc, TopUpNotFound):
        return status.HTTP_404_NOT_FOUND
    if isinstance(exc, DuplicateIdempotencyKey):
        return status.HTTP_409_CONFLICT
    if isinstance(exc, InsufficientFunds):
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_400_BAD_REQUEST


def _error_response(exc):
    return Response({'error': str(exc)}, status=_error_status(exc))


def _wallet_callback_url(request):
    configured = getattr(settings, 'WALLET_PAYMENT_CALLBACK_URL', '') or ''
    if configured and configured != 'callback':
        return configured
    return request.build_absolute_uri(reverse('wallet:topup-callback'))


def _frontend_topup_redirect(params):
    frontend_url = getattr(settings, 'FRONTEND_URL', '') or ''
    base = f"{frontend_url.rstrip('/')}/wallet/top-up/callback"
    return f"{base}?{urlencode(params)}"


@extend_schema(
    tags=['Wallet'],
    summary="Get current wallet balance",
    responses={200: get_api_response_serializer(WalletBalanceSerializer)},
)
class WalletBalanceView(views.APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, *args, **kwargs):
        balance = WalletService.get_balance(request.user)
        return Response({'balance': balance, 'currency': 'IRR'})


@extend_schema_view(
    list=extend_schema(
        tags=['Wallet'],
        summary="Paginated wallet transaction history",
        responses={200: get_paginated_response_serializer(WalletEntrySerializer)},
        parameters=[
            OpenApiParameter(name='page', required=False, type=int),
            OpenApiParameter(name='page_size', required=False, type=int),
        ],
    )
)
class WalletTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticatedUser]
    serializer_class = WalletEntrySerializer
    pagination_class = WalletEntryPagination
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        wallet = WalletService.get_or_create_wallet(self.request.user)
        return (
            WalletEntry.objects.filter(wallet=wallet)
            .select_related('order', 'topup')
            .order_by('-created_at', '-id')
        )


@extend_schema(
    tags=['Wallet'],
    summary="Start a wallet top-up via the payment gateway",
    request=StartTopUpSerializer,
    responses={
        201: get_api_response_serializer(TopUpStartResponseSerializer),
        200: get_api_response_serializer(TopUpStartResponseSerializer),
        400: ApiErrorResponseSerializer,
        409: ApiErrorResponseSerializer,
    },
)
class WalletTopUpStartView(views.APIView):
    permission_classes = [IsAuthenticatedUser]

    def post(self, request, *args, **kwargs):
        serializer = StartTopUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        metadata = {'source': 'api'}
        app = serializer.validated_data.get('app')
        if app:
            metadata['app'] = app
        try:
            topup, created = WalletService.start_topup(
                request.user,
                serializer.validated_data['amount'],
                serializer.validated_data['idempotency_key'],
                callback_url=_wallet_callback_url(request),
                metadata=metadata,
            )
        except (WalletError, TopUpGatewayError, DuplicateIdempotencyKey) as exc:
            return _error_response(exc)

        payload = {
            'public_id': topup.public_id,
            'amount': topup.amount,
            'status': topup.status,
            'idempotency_key': topup.idempotency_key,
            'payment_url': topup.payment_url,
            'authority': topup.gateway_authority,
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@extend_schema(
    tags=['Wallet'],
    summary="Query wallet top-up status",
    responses={
        200: get_api_response_serializer(TopUpSerializer),
        404: ApiErrorResponseSerializer,
    },
)
class WalletTopUpStatusView(views.APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, public_id, *args, **kwargs):
        try:
            topup = WalletService.get_topup(request.user, public_id)
        except TopUpNotFound as exc:
            return _error_response(exc)
        return Response(TopUpSerializer(topup).data)


@extend_schema(
    tags=['Wallet'],
    summary="Handles Zarinpal callback after a wallet top-up",
    description="This endpoint does not return JSON. It redirects the user back to the frontend.",
    responses={302: None},
)
class WalletTopUpCallbackView(views.APIView):
    permission_classes = []
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        authority = request.GET.get('Authority')
        status_param = request.GET.get('Status')
        if not authority:
            return redirect(_frontend_topup_redirect({
                'success': 'false',
                'message': 'Invalid callback parameters',
            }))

        if status_param != 'OK':
            try:
                topup = WalletService.mark_topup_failed(authority, metadata={'note': 'gateway_cancelled'})
            except TopUpNotFound:
                return redirect(_frontend_topup_redirect({
                    'success': 'false',
                    'message': 'Top-up not found',
                }))
            return redirect(_frontend_topup_redirect({
                'success': 'false',
                'message': 'Payment cancelled or failed',
                'topup_id': str(topup.public_id),
            }))

        try:
            topup, _created = WalletService.credit_verified_topup(authority)
        except TopUpNotFound:
            return redirect(_frontend_topup_redirect({
                'success': 'false',
                'message': 'Top-up not found',
            }))
        except WalletError as exc:
            logger.exception("Wallet top-up callback failed for authority=%s", authority)
            return redirect(_frontend_topup_redirect({
                'success': 'false',
                'message': str(exc),
            }))

        if topup.status == topup.STATUS_CREDITED:
            return redirect(_frontend_topup_redirect({
                'success': 'true',
                'message': 'Wallet topped up',
                'topup_id': str(topup.public_id),
            }))
        return redirect(_frontend_topup_redirect({
            'success': 'false',
            'message': 'Payment verification failed',
            'topup_id': str(topup.public_id),
        }))


@extend_schema(
    tags=['Wallet'],
    summary="Pay an entire order with wallet balance",
    request=PayOrderSerializer,
    responses={
        200: get_api_response_serializer(WalletPurchaseSerializer),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
        409: ApiErrorResponseSerializer,
    },
)
class WalletPayOrderView(views.APIView):
    permission_classes = [IsAuthenticatedUser]

    def post(self, request, *args, **kwargs):
        serializer = PayOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = get_object_or_404(
            Order,
            order_id=serializer.validated_data['order_id'],
            user=request.user,
        )
        try:
            entry, created = WalletService.pay_order(
                request.user,
                order,
                idempotency_key=serializer.validated_data.get('idempotency_key'),
                metadata={'source': 'wallet_pay_api'},
            )
        except (InsufficientFunds, InvalidAmount, OrderNotPayable, DuplicateIdempotencyKey, WalletError) as exc:
            return _error_response(exc)

        entry.wallet.refresh_from_db(fields=['balance'])
        return Response(
            {
                'entry_id': entry.pk,
                'order_id': order.order_id,
                'amount': entry.amount,
                'balance': entry.wallet.balance,
                'already_processed': not created,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=['Wallet - Admin'],
    summary="Create an administrative wallet adjustment",
    request=AdminAdjustmentSerializer,
    responses={
        200: get_api_response_serializer(AdminLedgerResultSerializer),
        400: ApiErrorResponseSerializer,
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    },
)
class WalletAdminAdjustView(views.APIView):
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        serializer = AdminAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = get_object_or_404(User, pk=serializer.validated_data['user_id'])
        try:
            entry, created = WalletService.admin_adjust(
                user=target,
                amount=serializer.validated_data['amount'],
                direction=serializer.validated_data['direction'],
                reason=serializer.validated_data['reason'],
                actor=request.user,
                idempotency_key=serializer.validated_data['idempotency_key'],
                metadata={'source': 'admin_api'},
            )
        except (InsufficientFunds, InvalidAmount, AdjustmentReasonRequired, DuplicateIdempotencyKey, WalletError) as exc:
            return _error_response(exc)

        entry.wallet.refresh_from_db(fields=['balance'])
        return Response({
            'entry_id': entry.pk,
            'entry_type': entry.entry_type,
            'direction': entry.direction,
            'amount': entry.amount,
            'balance': entry.wallet.balance,
            'already_processed': not created,
        })


@extend_schema(
    tags=['Wallet - Admin'],
    summary="Refund a previous wallet purchase debit",
    request=AdminRefundSerializer,
    responses={
        200: get_api_response_serializer(AdminLedgerResultSerializer),
        400: ApiErrorResponseSerializer,
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    },
)
class WalletAdminRefundView(views.APIView):
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        serializer = AdminRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = None
        original_entry = None
        if serializer.validated_data.get('order_id'):
            order = get_object_or_404(Order, order_id=serializer.validated_data['order_id'])
        if serializer.validated_data.get('entry_id'):
            original_entry = get_object_or_404(WalletEntry, pk=serializer.validated_data['entry_id'])

        try:
            entry, created = WalletService.refund_debit(
                actor=request.user,
                reason=serializer.validated_data['reason'],
                order=order,
                original_entry=original_entry,
                idempotency_key=serializer.validated_data.get('idempotency_key'),
                metadata={'source': 'admin_refund_api'},
            )
        except (RefundNotAllowed, AdjustmentReasonRequired, DuplicateIdempotencyKey, WalletError) as exc:
            return _error_response(exc)

        entry.wallet.refresh_from_db(fields=['balance'])
        return Response({
            'entry_id': entry.pk,
            'entry_type': entry.entry_type,
            'direction': entry.direction,
            'amount': entry.amount,
            'balance': entry.wallet.balance,
            'already_processed': not created,
        })
