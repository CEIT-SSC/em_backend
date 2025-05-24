from em_backend import settings
import requests


class ZarrinPal:
    merchant_id = settings.PAYMENT_API_KEY

    PAYMENT_DESCRIPTION = 'Register workshops or talks'
    CALLBACK_URL = settings.PAYMENT_CALLBACK_URL

    PAY_URL = "https://payment.zarinpal.com/pg/v4/payment/request.json"
    VERIFY_URL = "https://payment.zarinpal.com/pg/v4/payment/verify.json"
    START_PAY_URL = "https://payment.zarinpal.com/pg/StartPay/{authority}"

    STATUS_SUCCESS = 100
    STATUS_VERIFIED = 101
    STATUS_NOT_VALID = 9
    STATUS_API_KEY_ERROR = 10
    STATUS_FAILED = 51


    def generate_link(self, authority):
        link = self.START_PAY_URL
        return link.format(authority=authority)


    def create_payment(self, amount, mobile, email):
        data = {
            "merchant_id": self.merchant_id,
            "amount": amount * 10,  # Convert Toman to Rial
            "callback_url": self.CALLBACK_URL,
            "description": self.PAYMENT_DESCRIPTION,
            "metadata": {}
        }
        data["metadata"]["mobile"] = mobile
        data["metadata"]["email"] = email

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        try:
            response = requests.post(self.PAY_URL, json=data, headers=headers)
            response_data = response.json().get('data', {})
            code = response_data.get('code')

            if code == self.STATUS_SUCCESS:
                return {
                    'status': 'success',
                    'authority': response_data.get('authority'),
                    'error': None,
                    'link': self.generate_link(response_data.get('authority'))
                }
            else:
                return {
                    'status': 'failed',
                    'authority': None,
                    'error': response_data.get('message'),
                    'link': None
                }
        except requests.RequestException as e:
            return {
                'status': 'error',
                'authority': None,
                'error': str(e),
                'link': None
            }


    def verify_payment(self, authority, amount):
        data = {
            "merchant_id": self.merchant_id,
            "amount": amount * 10,  # Convert Toman to Rial
            "authority": authority
        }

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        try:
            response = requests.post(self.VERIFY_URL, json=data, headers=headers)
            print(response.json())
            response_data = response.json().get('data', {})
            code = response_data.get('code')

            if code == self.STATUS_SUCCESS or code == self.STATUS_VERIFIED:
                return {
                    'status': 'success',
                    'ref_id': response_data.get('ref_id'),
                    'error': None,
                    'card_pan': response_data.get('card_pan'),
                }
            else:
                return {
                    'status': 'failed',
                    'ref_id': None,
                    'error': response_data.get('message'),
                    'card_pan': None,
                }
        except requests.RequestException as e:
            return {
                'status': 'unexpected',
                'ref_id': None,
                'error': str(e),
                'card_pan': None,
            }
