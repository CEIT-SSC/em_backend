from em_backend import settings
import requests


class ZarrinPal:
    merchant_id = settings.PAYMENT_API_KEY
    PAYMENT_DESCRIPTION = 'Register workshops or talks'
    CALLBACK_URL = settings.PAYMENT_CALLBACK_URL

    BASE = "https://sandbox.zarinpal.com" if getattr(settings, "ZARINPAL_SANDBOX", False) else "https://payment.zarinpal.com"
    PAY_URL = f"{BASE}/pg/v4/payment/request.json"
    VERIFY_URL = f"{BASE}/pg/v4/payment/verify.json"
    START_PAY_URL = f"{BASE}/pg/StartPay/{{authority}}"
    UNVERIFIED_URL = f"{BASE}/pg/v4/payment/unVerified.json"

    STATUS_SUCCESS = 100
    STATUS_VERIFIED = 101

    def generate_link(self, authority):
        return self.START_PAY_URL.format(authority=authority)
    

    def list_unverified(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        body = {"merchant_id": self.merchant_id}
        resp = requests.post(self.UNVERIFIED_URL, json=body, headers=headers)
        payload = resp.json() if resp.content else {}
        data = payload.get("data") or {}
        if data.get("code") == 100:
            return data.get("authorities") or []
        return []

    def create_payment(self, amount, mobile, email, order_id=None):
        data = {
            "merchant_id": self.merchant_id,
            "amount": int(amount) * 10,
            "callback_url": self.CALLBACK_URL,
            "description": self.PAYMENT_DESCRIPTION,
            "metadata": {}
        }
        if mobile:
            data["metadata"]["mobile"] = mobile
        if email:
            data["metadata"]["email"] = email
        if order_id is not None:
            data["metadata"]["order_id"] = str(order_id)

        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        try:
            resp = requests.post(self.PAY_URL, json=data, headers=headers)
            payload = resp.json() if resp.content else {}
            data_block = payload.get("data") or {}
            errors_block = payload.get("errors")

            if data_block.get("code") == self.STATUS_SUCCESS and data_block.get("authority"):
                return {
                    "status": "success",
                    "authority": data_block.get("authority"),
                    "error": None,
                    "link": self.generate_link(data_block.get("authority"))
                }

            msg = None
            if isinstance(errors_block, dict) and errors_block:
                m = errors_block.get("message")
                c = errors_block.get("code")
                if m:
                    msg = f"{c}: {m}" if c is not None else m
                else:
                    parts = []
                    for k, v in errors_block.items():
                        if isinstance(v, (list, tuple)):
                            parts.append(f"{k}: " + " | ".join(map(str, v)))
                        else:
                            parts.append(f"{k}: {v}")
                    msg = " | ".join(parts)
            elif isinstance(errors_block, list) and errors_block:
                parts = []
                for e in errors_block:
                    if isinstance(e, dict) and "message" in e:
                        parts.append(str(e["message"]))
                    else:
                        parts.append(str(e))
                msg = " | ".join(parts)
            if not msg:
                msg = payload.get("message") or data_block.get("message") or "Payment request failed."

            return {"status": "failed", "authority": None, "error": msg, "link": None}

        except requests.RequestException as e:
            return {"status": "error", "authority": None, "error": str(e), "link": None}

    def verify_payment(self, authority, amount):
        data = {
            "merchant_id": self.merchant_id,
            "amount": int(amount) * 10,
            "authority": authority
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        try:
            resp = requests.post(self.VERIFY_URL, json=data, headers=headers)
            payload = resp.json() if resp.content else {}
            data_block = payload.get("data") or {}
            errors_block = payload.get("errors")

            if data_block.get("code") in (self.STATUS_SUCCESS, self.STATUS_VERIFIED):
                return {
                    "status": "success",
                    "ref_id": data_block.get("ref_id"),
                    "error": None,
                    "card_pan": data_block.get("card_pan")
                }

            msg = None
            if isinstance(errors_block, dict) and errors_block.get("message"):
                c = errors_block.get("code")
                m = errors_block.get("message")
                msg = f"{c}: {m}" if c is not None else m
            elif isinstance(errors_block, list) and errors_block:
                parts = []
                for e in errors_block:
                    if isinstance(e, dict) and "message" in e:
                        parts.append(str(e["message"]))
                    else:
                        parts.append(str(e))
                msg = " | ".join(parts)
            if not msg:
                msg = payload.get("message") or data_block.get("message") or "Payment verification failed."

            return {"status": "failed", "ref_id": None, "error": msg, "card_pan": None}

        except requests.RequestException as e:
            return {"status": "unexpected", "ref_id": None, "error": str(e), "card_pan": None}
