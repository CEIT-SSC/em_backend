from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from accounts.models import validate_phone_number

CustomUser = get_user_model()


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    password_confirm = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    phone_number = serializers.CharField(required=True, max_length=20)

    class Meta:
        model = CustomUser
        fields = ('email', 'password', 'password_confirm', 'first_name', 'last_name', 'phone_number')
        extra_kwargs = {
            'first_name': {'required': False, 'allow_blank': True, 'default': ''},
            'last_name': {'required': False, 'allow_blank': True, 'default': ''},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({"password_confirm": "Password fields didn't match."})

        try:
            normalized_phone = validate_phone_number(attrs['phone_number'])
            if CustomUser.objects.filter(phone_number=normalized_phone).exists():
                raise serializers.ValidationError("A user with this phone number already exists.")

            attrs['phone_number'] = normalized_phone
        except serializers.ValidationError as e:
            raise serializers.ValidationError({"phone_number": str(e)})

        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = CustomUser.objects.create_user(
            email=validated_data['email'],
            phone_number=validated_data['phone_number'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name'),
            last_name=validated_data.get('last_name'),
        )
        return user


class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    code = serializers.CharField(required=True, max_length=6, min_length=6)


class ResendVerificationEmailSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('email', 'first_name', 'last_name', 'phone_number', 'profile_picture', 'date_joined')
        read_only_fields = ('email', 'date_joined')


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


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True, style={'input_type': 'password'})
    new_password = serializers.CharField(required=True, write_only=True, style={'input_type': 'password'})
    new_password_confirm = serializers.CharField(required=True, write_only=True, style={'input_type': 'password'})

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError(_("Your old password was entered incorrectly. Please enter it again."))
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError({"new_password_confirm": "New password fields didn't match."})
        return attrs

    def save(self, **kwargs):
        password = self.validated_data['new_password']
        user = self.context['request'].user
        user.set_password(password)
        user.save()
        return user


class SimpleForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
