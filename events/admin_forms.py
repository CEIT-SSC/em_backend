"""Forms for editing a team's current roster without changing registrations."""
from django import forms
from django.forms.models import BaseInlineFormSet
from django.utils import timezone

from .models import CompetitionTeam, TeamMembership, invitation_expiry


class CompetitionTeamAdminForm(forms.ModelForm):
    class Meta:
        model = CompetitionTeam
        fields = ('name', 'leader')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['leader'].queryset = self.fields['leader'].queryset.filter(
                team_memberships__team=self.instance,
                team_memberships__status=TeamMembership.STATUS_ACCEPTED,
            )
        self.fields['leader'].help_text = (
            'Choose an accepted member. Save newly added members before changing the leader. '
            'Existing competition rosters and prices remain unchanged.'
        )

    def clean_name(self):
        name = self.cleaned_data['name']
        if CompetitionTeam.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('A team with this name already exists.')
        return name


class TeamMembershipAdminForm(forms.ModelForm):
    class Meta:
        model = TeamMembership
        fields = ('user', 'team', 'status')
        help_texts = {
            'status': 'Accepted members belong to the team immediately. '
                      'Use Rejected to remove a member from the current roster. '
                      'Existing competition rosters and prices remain unchanged.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_status = self.instance.status if self.instance.pk else None
        if self.instance.pk:
            # Membership identity is immutable; changing it would lose history.
            for field in ('user', 'team'):
                if field in self.fields:
                    self.fields[field].disabled = True
        else:
            self.initial.setdefault('status', TeamMembership.STATUS_ACCEPTED)

    def clean(self):
        data = super().clean()
        team, user = data.get('team'), data.get('user')
        if (team and user and user.pk == team.leader_id
                and data.get('status') != TeamMembership.STATUS_ACCEPTED):
            self.add_error('status', 'The team leader must remain an accepted member.')
        return data

    def save(self, commit=True):
        if self.original_status != self.instance.status:
            pending = self.instance.status == TeamMembership.STATUS_PENDING
            self.instance.expires_at = invitation_expiry() if pending else None
            self.instance.responded_at = None if pending else timezone.now()
        return super().save(commit=commit)


class TeamMembershipInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        accepted_ids = set()
        for form in self.forms:
            user = form.cleaned_data.get('user') or (
                form.instance.user if form.instance.pk else None
            )
            status = form.cleaned_data.get('status', form.instance.status)
            if user and status == TeamMembership.STATUS_ACCEPTED:
                accepted_ids.add(user.pk)
        # Check the submitted roster against the submitted leader, including
        # leader transfers and membership edits in the same admin request.
        if self.instance.leader_id not in accepted_ids:
            raise forms.ValidationError('The team leader must remain an accepted member.')
