import uuid
from django.core.files.base import ContentFile
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from shop.models import Cart
import requests


class CustomAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = sociallogin.account.extra_data
        user.is_active = True
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

        try:
            Cart.objects.get_or_create(user=user)
        except Exception:
            pass

        return user
