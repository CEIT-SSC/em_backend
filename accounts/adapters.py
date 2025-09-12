import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from shop.models import Cart
import requests

User = get_user_model()


class CustomAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def is_auto_signup_allowed(self, request, sociallogin):
        email = (sociallogin.user.email or "").strip()
        if not email:
            raise ValidationError(
                "GitHub did not provide an email address. Please ensure your primary GitHub email is public or verified.")
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

        full_name = extra_data.get('name', '')
        if not full_name:
            full_name = extra_data.get('login', '')

        if full_name:
            name_parts = full_name.split(' ', 1)
            user.first_name = name_parts[0]
            if len(name_parts) > 1:
                user.last_name = name_parts[1]
            else:
                user.last_name = ''

        if not user.phone_number or len(user.phone_number) == 0:
            user.phone_number = None

        picture_url = extra_data.get('avatar_url')
        if picture_url:
            try:
                response = requests.get(picture_url, stream=True, timeout=5)
                if response.status_code == 200:
                    filename = f"user_{uuid.uuid4().hex[:6]}_profile.jpg"
                    user.profile_picture.save(filename, ContentFile(response.content), save=False)
            except Exception as e:
                print(f"Could not download profile picture for {user.email}. Error: {e}")
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