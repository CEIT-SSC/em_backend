from decimal import Decimal
from json import JSONDecodeError
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.urls import reverse

from wallet.exceptions import TopUpGatewayError


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value) if isinstance(value, (int, float)) else False


def get_wallet_callback_url(request):
    configured = (getattr(settings, 'WALLET_PAYMENT_CALLBACK_URL', '') or '').strip()
    if configured:
        parsed = urlparse(configured)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise TopUpGatewayError("Wallet payment callback URL is invalid.")
        return configured
    return request.build_absolute_uri(reverse('wallet:topup-callback'))


class ZarrinPal:
    """The gateway client used only to fund wallets."""

    STATUS_SUCCESS = 100
    STATUS_VERIFIED = 101

    def __init__(self):
        self.merchant_id = getattr(settings, 'PAYMENT_API_KEY', '')
        self.description = getattr(settings, 'PAYMENT_DESCRIPTION', 'Wallet top-up')
        sandbox = _to_bool(getattr(settings, 'ZARINPAL_SANDBOX', False))
        base = 'https://sandbox.zarinpal.com' if sandbox else 'https://payment.zarinpal.com'
        self.pay_url = f'{base}/pg/v4/payment/request.json'
        self.verify_url = f'{base}/pg/v4/payment/verify.json'
        self.start_pay_url = f'{base}/pg/StartPay/{{authority}}'

    @staticmethod
    def _gateway_amount(amount):
        return int(Decimal(str(amount)) * Decimal('10'))

    def create_payment(self, amount, mobile, email, order_id=None, callback_url=None):
        payload = {
            'merchant_id': self.merchant_id,
            'amount': self._gateway_amount(amount),
            'callback_url': callback_url,
            'description': self.description,
            'metadata': {},
        }
        if mobile:
            payload['metadata']['mobile'] = mobile
        if email:
            payload['metadata']['email'] = email
        if order_id is not None:
            payload['metadata']['order_id'] = str(order_id)

        try:
            response = requests.post(
                self.pay_url,
                json=payload,
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                timeout=15,
            )
            body = response.json() if response.content else {}
        except requests.RequestException as exc:
            return {'status': 'error', 'authority': None, 'error': str(exc), 'link': None}
        except (ValueError, JSONDecodeError):
            return {
                'status': 'error', 'authority': None,
                'error': 'Invalid response from payment gateway.', 'link': None,
            }

        data = body.get('data') or {}
        if data.get('code') == self.STATUS_SUCCESS and data.get('authority'):
            authority = data['authority']
            return {
                'status': 'success',
                'authority': authority,
                'error': None,
                'link': self.start_pay_url.format(authority=authority),
            }
        return {
            'status': 'failed',
            'authority': None,
            'error': self._error_message(body, 'Payment request failed.'),
            'link': None,
        }

    def verify_payment(self, authority, amount):
        payload = {
            'merchant_id': self.merchant_id,
            'amount': self._gateway_amount(amount),
            'authority': authority,
        }
        try:
            response = requests.post(
                self.verify_url,
                json=payload,
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                timeout=15,
            )
            body = response.json() if response.content else {}
        except requests.RequestException as exc:
            return {'status': 'unexpected', 'ref_id': None, 'error': str(exc), 'card_pan': None}
        except (ValueError, JSONDecodeError):
            return {
                'status': 'unexpected', 'ref_id': None,
                'error': 'Invalid response from payment gateway.', 'card_pan': None,
            }

        data = body.get('data') or {}
        if data.get('code') in {self.STATUS_SUCCESS, self.STATUS_VERIFIED}:
            return {
                'status': 'success',
                'ref_id': data.get('ref_id'),
                'error': None,
                'card_pan': data.get('card_pan'),
            }
        return {
            'status': 'failed',
            'ref_id': None,
            'error': self._error_message(body, 'Payment verification failed.'),
            'card_pan': None,
        }

    @staticmethod
    def _error_message(body, fallback):
        errors = body.get('errors') or {}
        if isinstance(errors, dict):
            message = errors.get('message')
            code = errors.get('code')
            if message:
                return f'{code}: {message}' if code is not None else str(message)
        if isinstance(errors, list):
            messages = [
                str(item.get('message')) if isinstance(item, dict) and item.get('message') else str(item)
                for item in errors
            ]
            if messages:
                return ' | '.join(messages)
        data = body.get('data') or {}
        return str(body.get('message') or data.get('message') or fallback)
