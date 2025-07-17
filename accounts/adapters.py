from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.files.base import ContentFile
import requests
import uuid

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
                response = requests.get(picture_url, stream=True)
                if response.status_code == 200:
                    filename = f"user_{uuid.uuid4().hex[:6]}_profile.jpg"
                    user.profile_picture.save(filename, ContentFile(response.content), save=False)
            except (requests.RequestException, IOError):
                pass

        return user
