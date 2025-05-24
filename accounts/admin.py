from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import CustomUser


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = CustomUser
        fields = ('email', 'phone_number', 'first_name', 'last_name')


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = (
            'email', 'phone_number', 'first_name', 'last_name',
            'is_active', 'is_staff', 'is_superuser',
            'groups', 'user_permissions',
            'email_verification_code', 'email_verification_code_expires_at',
            'last_login', 'date_joined'
        )


class CustomUserAdmin(BaseUserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm

    model = CustomUser
    list_display = ('email', 'phone_number', 'first_name', 'last_name', 'is_staff', 'is_active', 'date_joined')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups')
    search_fields = ('email', 'phone_number', 'first_name', 'last_name')
    ordering = ('email',)


    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone_number', 'profile_picture')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser',
                                    'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
        ('Email Verification', {'fields': ('email_verification_code', 'email_verification_code_expires_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'phone_number', 'password', 'password2',
                       'first_name', 'last_name', 'profile_picture',
                       'is_staff', 'is_superuser',
                       'groups', 'user_permissions'),
        }),
    )
    readonly_fields = ('last_login', 'date_joined')


admin.site.register(CustomUser, CustomUserAdmin)
