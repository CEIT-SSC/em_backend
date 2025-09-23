import string
import random
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models, transaction, IntegrityError
from django.utils import timezone
import re
from django.core.exceptions import ValidationError


def _random_alnum(length=8):
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(random.choice(alphabet) for _ in range(length))


def generate_unique_sky_username():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    for _ in range(1000):
        candidate = _random_alnum(8)
        if not User.objects.filter(sky_username=candidate).exists():
            return candidate
    raise RuntimeError("Failed to generate unique sky_username after many attempts")


def generate_sky_password():
    return _random_alnum(8)


def validate_image_size(image):
    max_size_mb = 2
    if image.size > max_size_mb * 1024 * 1024:
        raise ValidationError(f"Image size should not exceed {max_size_mb}MB.")


def validate_phone_number(value):
    if value is None or len(value) == 0: return None

    cleaned = re.sub(r'\D', '', value)
    if not re.fullmatch(r'9\d{9}', cleaned):
        raise ValidationError("Enter a valid Iranian phone number.")

    normalized = cleaned

    if len(normalized) != 10 or not normalized.startswith('9'):
        raise ValidationError("Invalid phone number format after normalization.")

    return normalized


class CustomUserManager(BaseUserManager):
    def _create_user_and_cart(self, email, password, phone_number=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')

        email = self.normalize_email(email)
        user = self.model(email=email, phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        from shop.models import Cart
        Cart.objects.create(user=user)
        return user

    def create_user(self, email, password=None, phone_number=None, first_name=None, last_name=None,
                    **other_extra_fields):
        actual_extra_fields = other_extra_fields.copy()
        if first_name is not None:
            actual_extra_fields['first_name'] = first_name
        if last_name is not None:
            actual_extra_fields['last_name'] = last_name

        actual_extra_fields.setdefault('is_staff', False)
        actual_extra_fields.setdefault('is_superuser', False)

        return self._create_user_and_cart(email, password, phone_number=phone_number, **actual_extra_fields)

    def create_superuser(self, email, password=None, phone_number=None, first_name=None, last_name=None,
                         **other_extra_fields):
        actual_extra_fields = other_extra_fields.copy()
        if first_name is not None:
            actual_extra_fields['first_name'] = first_name
        if last_name is not None:
            actual_extra_fields['last_name'] = last_name

        actual_extra_fields.setdefault('is_staff', True)
        actual_extra_fields.setdefault('is_superuser', True)
        actual_extra_fields.setdefault('is_active', True)

        if actual_extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if actual_extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user_and_cart(email, password, phone_number=phone_number, **actual_extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(
        unique=True,
        blank=False,
        error_messages={'unique': "A user with that email already exists."},
        verbose_name="Email Address"
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=False,
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

    sky_username = models.CharField(
        max_length=8,
        unique=True,
        blank=True,
        null=True,
        verbose_name="Online Class Username",
        help_text="8-character unique username for online classes."
    )

    sky_password = models.CharField(
        max_length=8,
        blank=True,
        null=True,
        verbose_name="Online Class Password",
        help_text="8-character password for online classes."
    )

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    def get_full_name(self):
        full_name = '%s %s' % (self.first_name, self.last_name)
        return full_name.strip()

    def get_short_name(self):
        return self.first_name

    def save(self, *args, **kwargs):
        if not self.sky_username:
            for attempt in range(5):
                candidate = generate_unique_sky_username()
                self.sky_username = candidate
                if not self.sky_password:
                    self.sky_password = generate_sky_password()
                try:
                    with transaction.atomic():
                        super(CustomUser, self).save(*args, **kwargs)
                    break
                except IntegrityError:
                    self.sky_username = None
                    continue
            else:
                raise IntegrityError("Could not generate unique sky_username after retries")
        else:
            if not self.sky_password:
                self.sky_password = generate_sky_password()
            super(CustomUser, self).save(*args, **kwargs)

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ['email']


class Staff(models.Model):
    name = models.CharField(max_length=200)
    team = models.CharField(max_length=200, null=True, blank=True)
    role = models.CharField(max_length=100)
    description = models.TextField()
    picture = models.ImageField(upload_to='staff_pictures/')
    social_account_link = models.URLField(max_length=300, blank=True, null=True)
    Github_link = models.URLField(max_length=300, blank=True, null=True)

    def __str__(self):
        return self.name