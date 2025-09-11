import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from em_backend import settings
from shop.models import Cart
import requests
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter

User = get_user_model()

class CustomAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def is_auto_signup_allowed(self, request, sociallogin):
        email = (sociallogin.user.email or "").strip()
        if not email:
            raise ValidationError("Provider did not supply an email.")
        return True

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            return

        email = (sociallogin.user.email or "").strip().lower()
        if not email:
            return

        try:
            existing_user = User.objects.get(email=email)
        except User.DoesNotExist:
            return

        sociallogin.connect(request, existing_user)

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = sociallogin.account.extra_data
        user.first_name = extra_data.get('given_name', '')
        user.last_name = extra_data.get('family_name', '')
        if not user.phone_number or len(user.phone_number) == 0:
            user.phone_number = None

        picture_url = extra_data.get('picture')
        if picture_url:
            try:
                response = requests.get(picture_url, stream=True, timeout=5)
                if response.status_code == 200:
                    filename = f"user_{uuid.uuid4().hex[:6]}_profile.jpg"
                    user.profile_picture.save(filename, ContentFile(response.content), save=False)
            except Exception:
                pass

        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        if not user.is_active:
            user.is_active = True
            user.save()

        try:
            Cart.objects.get_or_create(user=user)
        except Exception:
            pass

        return user


class ProxiedGoogleOAuth2Adapter(GoogleOAuth2Adapter):
    access_token_url = settings.GOOGLE_ACCESS_TOKEN_URL
    profile_url = settings.GOOGLE_Profile_URL
