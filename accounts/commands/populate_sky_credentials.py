from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()

class Command(BaseCommand):
    help = "Populate missing sky_username and sky_password for existing users."

    def handle(self, *args, **options):
        users = User.objects.filter(models.Q(sky_username__isnull=True) | models.Q(sky_username='') |
                                    models.Q(sky_password__isnull=True) | models.Q(sky_password=''))
        total = users.count()
        self.stdout.write(f"Found {total} users that need sky credentials.")
        i = 0
        for u in users.iterator():
            i += 1
            changed = False
            if not u.sky_username:
                u.sky_username = u.__class__.objects.model.generate_unique_sky_username() if hasattr(u.__class__.objects.model, 'generate_unique_sky_username') else None
                if not u.sky_username:
                    from accounts.models import generate_unique_sky_username
                    u.sky_username = generate_unique_sky_username()
                changed = True
            if not u.sky_password:
                from accounts.models import generate_sky_password
                u.sky_password = generate_sky_password()
                changed = True
            if changed:
                try:
                    with transaction.atomic():
                        u.save()
                except Exception as e:
                    self.stderr.write(f"Failed to save user {u.id} ({u.email}): {e}")
        self.stdout.write("Done.")
