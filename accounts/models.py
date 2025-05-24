from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class CustomUserManager(BaseUserManager):
    def _create_user_and_cart(self, email, password, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        from shop.models import Cart  # Local import
        Cart.objects.create(user=user)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user_and_cart(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user_and_cart(email, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(
        unique=True,
        blank=False,
        error_messages={'unique': "A user with that email already exists."},
        verbose_name="Email Address"
    )
    first_name = models.CharField(max_length=150, blank=True, verbose_name="First Name")
    last_name = models.CharField(max_length=150, blank=True, verbose_name="Last Name")

    is_staff = models.BooleanField(default=False, verbose_name="Staff Status")
    is_active = models.BooleanField(default=False, verbose_name="Active Status")  # Activate after email verification
    date_joined = models.DateTimeField(default=timezone.now, verbose_name="Date Joined")

    phone_number = models.CharField(max_length=20, blank=True, null=True, verbose_name="Phone Number")
    profile_picture = models.ImageField(upload_to='profile_pics/%Y/%m/', blank=True, null=True,
                                        verbose_name="Profile Picture")
    email_verification_code = models.CharField(max_length=6, blank=True, null=True,
                                               verbose_name="Email Verification Code")
    email_verification_code_expires_at = models.DateTimeField(blank=True, null=True,
                                                              verbose_name="Verification Code Expiry")

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

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ['email']
