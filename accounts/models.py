from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
import re
from django.core.exceptions import ValidationError


def validate_image_size(image):
    max_size_mb = 2
    if image.size > max_size_mb * 1024 * 1024:
        raise ValidationError(f"Image size should not exceed {max_size_mb}MB.")


def validate_phone_number(value):
    cleaned = re.sub(r'[^\d]', '', value)
    if not re.match(r'^(0|98)?9\d{9}$', cleaned):
        raise ValidationError("Enter a valid Iranian phone number.")

    if cleaned.startswith('98'):
        normalized = cleaned[2:]
    elif cleaned.startswith('0'):
        normalized = cleaned[1:]
    else:
        normalized = cleaned

    if len(normalized) != 10 or not normalized.startswith('9'):
        raise ValidationError("Invalid phone number format after normalization.")

    return normalized


class CustomUserManager(BaseUserManager):
    def _create_user_and_cart(self, email, phone_number, password, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        if not phone_number:
            raise ValueError('The Phone Number field must be set')

        email = self.normalize_email(email)
        user = self.model(email=email, phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        from shop.models import Cart
        Cart.objects.create(user=user)
        return user

    def create_user(self, email, phone_number, password=None, first_name=None, last_name=None, **other_extra_fields):
        actual_extra_fields = other_extra_fields.copy()
        if first_name is not None:
            actual_extra_fields['first_name'] = first_name
        if last_name is not None:
            actual_extra_fields['last_name'] = last_name

        actual_extra_fields.setdefault('is_staff', False)
        actual_extra_fields.setdefault('is_superuser', False)

        return self._create_user_and_cart(email, phone_number, password, **actual_extra_fields)

    def create_superuser(self, email, phone_number, password=None, first_name=None, last_name=None,
                         **other_extra_fields):
        actual_extra_fields = other_extra_fields.copy()
        if first_name is not None:
            actual_extra_fields['first_name'] = first_name
        if last_name is not None:
            actual_extra_fields['last_name'] = last_name

        actual_extra_fields.setdefault('is_staff', True)
        actual_extra_fields.setdefault('is_superuser', True)
        actual_extra_fields.setdefault('is_active', True)  # Superusers should be active by default

        if actual_extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if actual_extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user_and_cart(email, phone_number, password, **actual_extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(
        unique=True,
        blank=False,
        error_messages={'unique': "A user with that email already exists."},
        verbose_name="Email Address"
    )
    phone_number = models.CharField(
        max_length=20,
        blank=False,
        null=False,
        unique=True,
        verbose_name="Phone Number",
        validators=[validate_phone_number,],
        error_messages={'unique': "A user with this phone number already exists.",},
    )

    first_name = models.CharField(max_length=150, blank=True, verbose_name="First Name")
    last_name = models.CharField(max_length=150, blank=True, verbose_name="Last Name")

    is_staff = models.BooleanField(default=False, verbose_name="Staff Status")
    is_active = models.BooleanField(default=False, verbose_name="Active Status")
    date_joined = models.DateTimeField(default=timezone.now, verbose_name="Date Joined")

    profile_picture = models.ImageField(upload_to='profile_pics/%Y/%m/', blank=True, null=True,
                                        verbose_name="Profile Picture", validators=[validate_image_size])
    email_verification_code = models.CharField(max_length=6, blank=True, null=True,
                                               verbose_name="Email Verification Code")
    email_verification_code_expires_at = models.DateTimeField(blank=True, null=True,
                                                              verbose_name="Verification Code Expiry")

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['phone_number']

    def __str__(self):
        return self.email

    def get_full_name(self):
        full_name = '%s %s' % (self.first_name, self.last_name)
        return full_name.strip()

    def get_short_name(self):
        return self.first_name

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ['email']
