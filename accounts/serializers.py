from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from accounts.models import validate_phone_number, Staff
from django_typomatic import ts_interface
from dj_rest_auth.registration.serializers import SocialLoginSerializer as BaseSocialLoginSerializer

CustomUser = get_user_model()

@ts_interface()
class HandshakeTokenSerializer(serializers.Serializer):
    handshake_token = serializers.CharField()

@ts_interface()
class TokenRequestSerializer(serializers.Serializer):
    grant_type = serializers.ChoiceField(
        choices=["password", "refresh_token"],
        help_text="The grant type. Use 'password' for user login, 'refresh_token' for refreshing an expired access token."
    )
    username = serializers.CharField(required=False, help_text="Required for grant_type='password'. The user's email.")
    password = serializers.CharField(required=False, help_text="Required for grant_type='password'.")
    refresh_token = serializers.CharField(required=False, help_text="Required for grant_type='refresh_token'.")
    client_id = serializers.CharField(required=True, help_text="The client ID of your application.")
    client_secret = serializers.CharField(required=False, help_text="The client secret, if your application is confidential.")

@ts_interface()
class RevokeTokenRequestSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="The access or refresh token to be revoked.")
    client_id = serializers.CharField(help_text="The client ID of your application.")
    client_secret = serializers.CharField(required=False, help_text="The client secret, if your application is confidential.")
    token_type_hint = serializers.ChoiceField(choices=["access_token", "refresh_token"], required=False,
                                              help_text="Optional hint about the type of token.")

@ts_interface()
class SocialLoginSerializer(BaseSocialLoginSerializer):
    pass


@ts_interface()
class AuthorizationFormSerializer(serializers.Serializer):
    username = serializers.CharField(required=True, help_text="The user's email address.")
    password = serializers.CharField(required=True, write_only=True, style={'input_type': 'password'})

    client_id = serializers.CharField(required=True)
    redirect_uri = serializers.URLField(required=True)
    response_type = serializers.CharField(required=True, help_text="Must be 'code'.")
    scope = serializers.CharField(required=False)
    code_challenge = serializers.CharField(required=False)
    code_challenge_method = serializers.CharField(required=False)

    allow = serializers.CharField(required=True,
                                  help_text="Must be a truthy value like 'true' to indicate user consent.")

@ts_interface()
class TokenSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    expires_in = serializers.IntegerField()
    token_type = serializers.CharField()
    scope = serializers.CharField()
    refresh_token = serializers.CharField()


@ts_interface()
class UserRegistrationSuccessSerializer(serializers.Serializer):
    email   = serializers.EmailField()


@ts_interface()
class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    phone_number = serializers.CharField(required=True, max_length=20)

    class Meta:
        model = CustomUser
        fields = ('email', 'password', 'first_name', 'last_name', 'phone_number')
        extra_kwargs = {
            'first_name': {'required': False, 'allow_blank': True, 'default': ''},
            'last_name': {'required': False, 'allow_blank': True, 'default': ''},
            'phone_number': {'required': False, 'allow_blank': True, 'default': None},
        }

    def validate(self, attrs):
        try:
            normalized_phone = validate_phone_number(attrs['phone_number'])
            if CustomUser.objects.filter(phone_number=normalized_phone).exists():
                raise serializers.ValidationError({"phone_number": "A user with this phone number already exists."})

            attrs['phone_number'] = normalized_phone
        except serializers.ValidationError as e:
            raise serializers.ValidationError({"phone_number": str(e)})

        return attrs

    def create(self, validated_data):
        user = CustomUser.objects.create_user(
            email=validated_data['email'],
            phone_number=validated_data['phone_number'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name'),
            last_name=validated_data.get('last_name'),
        )
        return user


@ts_interface()
class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    code = serializers.CharField(required=True, max_length=6, min_length=6)


@ts_interface()
class ResendVerificationEmailSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)


@ts_interface()
class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('email', 'first_name', 'last_name', 'phone_number', 'profile_picture', 'date_joined')
        read_only_fields = ('email', 'date_joined')


@ts_interface()
class UserProfileUpdateSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(required=False, max_length=20, allow_blank=False)

    class Meta:
        model = CustomUser
        fields = ('first_name', 'last_name', 'phone_number', 'profile_picture')
        extra_kwargs = {
            'first_name': {'required': False},
            'last_name': {'required': False},
            'profile_picture': {'required': False, 'allow_null': True},
        }

    def validate_phone_number(self, value):
        normalized_phone = validate_phone_number(value)

        user = self.context['request'].user
        if CustomUser.objects.exclude(pk=user.pk).filter(phone_number=normalized_phone).exists():
            raise serializers.ValidationError("A user with this phone number already exists.")

        return normalized_phone

    def update(self, instance, validated_data):
        profile_picture = validated_data.get('profile_picture', Ellipsis)
        if profile_picture is None:
            if instance.profile_picture:
                instance.profile_picture.delete(save=False)
            instance.profile_picture = None
        elif profile_picture is not Ellipsis:
            instance.profile_picture = profile_picture

        if 'profile_picture' in validated_data:
            validated_data.pop('profile_picture')

        return super().update(instance, validated_data)


@ts_interface()
class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True, style={'input_type': 'password'})
    new_password = serializers.CharField(required=True, write_only=True, style={'input_type': 'password'})

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError(_("Your old password was entered incorrectly. Please enter it again."))
        return value

    def save(self, **kwargs):
        password = self.validated_data['new_password']
        user = self.context['request'].user
        user.set_password(password)
        user.save()
        return user


@ts_interface()
class SimpleForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)


@ts_interface()
class StaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = '__all__'