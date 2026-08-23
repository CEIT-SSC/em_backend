from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient


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
