from json import JSONDecodeError
from django.conf import settings
import requests


def _to_bool(v):
    if isinstance(v, bool): return v
    if isinstance(v, str):  return v.strip().lower() in {"1","true","yes","on"}
    if isinstance(v, (int, float)): return bool(v)
    return False


class ZarrinPal:
    STATUS_SUCCESS  = 100
    STATUS_VERIFIED = 101

    def __init__(self):
        self.merchant_id = getattr(settings, "PAYMENT_API_KEY", "")
        self.PAYMENT_DESCRIPTION = getattr(settings, "PAYMENT_DESCRIPTION", "Register workshops or talks")
        self.CALLBACK_URL = getattr(settings, "PAYMENT_CALLBACK_URL", "")

        sandbox = _to_bool(getattr(settings, "ZARINPAL_SANDBOX", False))
        self.BASE = "https://sandbox.zarinpal.com" if sandbox else "https://payment.zarinpal.com"

        self.PAY_URL         = f"{self.BASE}/pg/v4/payment/request.json"
        self.VERIFY_URL      = f"{self.BASE}/pg/v4/payment/verify.json"
        self.START_PAY_URL   = f"{self.BASE}/pg/StartPay/{{authority}}"
        self.UNVERIFIED_URL  = f"{self.BASE}/pg/v4/payment/unVerified.json"
        self.INQUIRY_URL   = f"{self.BASE}/pg/v4/payment/inquiry.json"

    def generate_link(self, authority):
        return self.START_PAY_URL.format(authority=authority)

    def list_unverified(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        payload = {"merchant_id": self.merchant_id}

        try:
            resp = requests.post(self.UNVERIFIED_URL, json=payload, headers=headers)
        except requests.RequestException:
            return []

        if resp.status_code < 200 or resp.status_code >= 300:
            return []

        try:
            data = resp.json()
        except (ValueError, JSONDecodeError):
            return []

        authorities = (
                data.get("data", {}) or {}
        ).get("authorities") if data.get("data", {}).get("code") == 100 else []

        return authorities or []

    def create_payment(self, amount, mobile, email, order_id=None):
        data = {
            "merchant_id": self.merchant_id,
            "amount": int(amount) * 10,
            "callback_url": self.CALLBACK_URL,
            "description": self.PAYMENT_DESCRIPTION,
            "metadata": {},
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


    def inquiry(self, *, authority: str):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        payload = {"merchant_id": self.merchant_id, "authority": authority}

        try:
            resp = requests.post(self.INQUIRY_URL, json=payload, headers=headers, timeout=10)
        except requests.RequestException as e:
            return {"status": "unexpected", "error": str(e)}

        if not resp.content:
            return {"status": "unexpected", "error": "Empty response from inquiry"}

        try:
            data = resp.json()
        except ValueError:
            return {"status": "unexpected", "error": "Invalid JSON from inquiry"}

        data_block   = (data or {}).get("data") or {}
        errors_block = (data or {}).get("errors") or {}

        status_txt = (data_block.get("status") or "").upper()
        if status_txt == "FAILED":
            return {"status": "failed", "error": None}
        if status_txt == "IN_BANK":
            return {"status": "in_bank", "error": None}

        err_msg = None
        if isinstance(errors_block, dict):
            err_msg = errors_block.get("message") or errors_block.get("code")
        return {"status": "not_found", "error": err_msg}