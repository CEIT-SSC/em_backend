from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import TeamMembership


class Command(BaseCommand):
    help = 'Mark unanswered team invitations past their expiry as expired.'

    def handle(self, *args, **options):
        count = TeamMembership.objects.filter(status=TeamMembership.STATUS_PENDING, expires_at__lte=timezone.now()).update(
            status=TeamMembership.STATUS_EXPIRED)
        self.stdout.write(f'Expired {count} invitation(s).')
