from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from .models import CustomUser


class SwaggerOAuthCompatibilityTest(TestCase):
    def test_token_endpoint_uses_standard_oauth_response_shape(self):
        response = APIClient().post(
            '/api/o/token/',
            {
                'grant_type': 'password',
                'username': 'invalid@example.com',
                'password': 'wrong',
                'client_id': 'invalid',
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'error': 'invalid_client'})

    def test_swagger_uses_only_the_oauth2_security_scheme(self):
        spectacular_settings = settings.SPECTACULAR_SETTINGS

        self.assertNotIn('APPEND_COMPONENTS', spectacular_settings)
        self.assertEqual(
            spectacular_settings['SECURITY'],
            [{'oauth2': ['read', 'write']}],
        )


class ForgotPasswordEmailLookupTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(
            email='Moein.Example@gmail.com', password='test-password'
        )

    @patch('accounts.views.send_email_async_task')
    def test_different_email_case_sends_to_stored_address(self, send_email):
        response = self.client.post(
            '/api/forgot-password/',
            {'email': 'moein.example@gmail.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.kwargs['recipient_list'], [self.user.email])

    @patch('accounts.views.send_email_async_task')
    def test_unknown_email_keeps_generic_response_without_sending(self, send_email):
        response = self.client.post(
            '/api/forgot-password/',
            {'email': 'unknown@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        send_email.assert_not_called()

    @patch('accounts.views.send_email_async_task')
    def test_ambiguous_case_insensitive_match_does_not_select_an_account(self, send_email):
        CustomUser.objects.create_user(
            email='MOEIN.EXAMPLE@gmail.com', password='test-password'
        )

        response = self.client.post(
            '/api/forgot-password/',
            {'email': 'moein.example@gmail.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        send_email.assert_not_called()

    @patch('accounts.views.send_email_async_task')
    def test_exact_match_still_works_when_case_variants_exist(self, send_email):
        CustomUser.objects.create_user(
            email='MOEIN.EXAMPLE@gmail.com', password='test-password'
        )

        response = self.client.post(
            '/api/forgot-password/',
            {'email': self.user.email},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.kwargs['recipient_list'], [self.user.email])
