from urllib.parse import urlparse
from django.contrib import admin, messages
from django.utils.html import format_html
from django.template.loader import render_to_string
from django.utils import timezone
from accounts.email_utils import send_email_async_task
from .models import (
    Presenter, Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    TeamContent, ContentImage, ContentLike, ContentComment,
    PresentationEnrollment, SoloCompetitionRegistration, Post, CompetitionTeamRegistration,
    RegistrationPrerequisite,
)
import re
import datetime
from io import BytesIO
from openpyxl import Workbook
from django.http import HttpResponse
from django.db import transaction
from django.shortcuts import render
from django import forms
from .admin_forms import (
    CompetitionTeamAdminForm, TeamMembershipAdminForm, TeamMembershipInlineFormSet,
)
from django.conf import settings
import http.client
import json
import ssl
import certifi
import logging

logger = logging.getLogger(__name__)


SMSIR_API_KEY = getattr(settings, "SMSIR_API_KEY", None)
SMSIR_LINE_NUMBER = getattr(settings, "SMSIR_LINE_NUMBER", None)

def send_sms(phone_number: str, message: str) -> bool:
    if not SMSIR_API_KEY or not SMSIR_LINE_NUMBER:
        logging.error("sms.ir API key or LINE_NUMBER not set in settings.")
        return False

    try:
        context = ssl.create_default_context(cafile=certifi.where())
        conn = http.client.HTTPSConnection("api.sms.ir", context=context)

        payload = json.dumps({
            "lineNumber": SMSIR_LINE_NUMBER,
            "messageText": message,
            "mobiles": [phone_number],
            "sendDateTime": None
        })

        headers = {
            'X-API-KEY': SMSIR_API_KEY,
            'Content-Type': 'application/json'
        }

        conn.request("POST", "/v1/send/bulk", payload, headers)
        res = conn.getresponse()
        data = res.read()
        response_text = data.decode("utf-8").strip()

        logging.info(f"sms.ir response: {response_text}")

        if not response_text:
            logging.error("sms.ir returned empty response")
            return False

        response_json = json.loads(response_text)
        if response_json.get("status") == 1:
            print("SMS sent successfully")
            return True
        else:
            logging.error(f"Failed to send SMS: {response_text}")
            return False

    except Exception as e:
        logging.error(f"Exception sending SMS: {e}")
        return False

def _format_datetime(dt):
    local_dt = timezone.localtime(dt)
    return local_dt.strftime('%Y/%m/%d %H:%M')


@admin.action(description='Export presentation participants to Excel (.xlsx)')
def export_presentation_enrollments(modeladmin, request, queryset):
    TARGET_STATUS = PresentationEnrollment.STATUS_COMPLETED_OR_FREE
    enrollments = PresentationEnrollment.objects.filter(
        presentation__in=queryset,
        status=TARGET_STATUS
    ).select_related('user', 'presentation', 'order_item', 'presentation__event')

    users_map = {}

    def extract_room_from_link(link):
        if not link:
            return ""
        try:
            p = urlparse(link)
            parts = [seg for seg in p.path.split('/') if seg]
            return parts[-1] if parts else ""
        except Exception:
            return ""

    for en in enrollments:
        user = en.user
        if user is None:
            continue
        uinfo = users_map.setdefault(user.id, {
            'user': user,
            'email': user.email or "",
            'phone_number': getattr(user, 'phone_number', '') or "",
            'full_name': (user.get_full_name() or (user.first_name or getattr(user, 'username', '') or user.email)),
            'presentations': set(),
            'rooms': set()
        })
        pres = en.presentation
        if pres:
            uinfo['presentations'].add(pres.title)
            room = extract_room_from_link(getattr(pres, 'online_link', None))
            if room:
                uinfo['rooms'].add(room)

    if not users_map:
        modeladmin.message_user(request, "No completed enrollments found for the selected presentations.", level=messages.WARNING)
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Participants"
    headers = ["email", "phone_number", "full_name", "sky_username", "sky_password", "presentations", "rooms"]
    ws.append(headers)

    from accounts.models import generate_sky_password, generate_unique_sky_username

    for uid, uinfo in users_map.items():
        user = uinfo['user']
        if not getattr(user, 'sky_username', None):
            for _ in range(5):
                candidate = generate_unique_sky_username()
                user.sky_username = candidate
                if not getattr(user, 'sky_password', None):
                    user.sky_password = generate_sky_password()
                try:
                    with transaction.atomic():
                        user.save()
                    break
                except Exception:
                    user.sky_username = None
                    continue
            if not user.sky_username:
                user.sky_username = f"U{user.id:07d}"[:8]
                user.sky_password = generate_sky_password()
                user.save()
        else:
            if not getattr(user, 'sky_password', None):
                user.sky_password = generate_sky_password()
                user.save()

        presentations_str = ",".join(sorted(uinfo['presentations']))
        rooms_str = ",".join(sorted(uinfo['rooms']))

        row = [
            user.email or "",
            getattr(user, 'phone_number', '') or "",
            uinfo['full_name'],
            user.sky_username or "",
            user.sky_password or "",
            presentations_str,
            rooms_str
        ]
        ws.append(row)

    ws.append([])
    ws.append([f"Exported from admin at {datetime.datetime.utcnow().replace(microsecond=0).isoformat()}Z"])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"presentation_participants_{ts}.xlsx"

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@admin.action(description='Send reminder email to presentation participants')
def send_presentation_reminder(modeladmin, request, queryset):
    total = 0
    for pres in queryset:
        qs = pres.enrollments.filter(status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE)
        emails = list(qs.values_list('user__email', flat=True))
        if not emails:
            continue
        html = render_to_string('reminder.html', {
            'object': pres,
            'object_datetime': _format_datetime(pres.start_time),
            'object_location': pres.online_link if pres.is_online else pres.location or '',
        })
        subject = f'یادآوری ارائه: {pres.title}'
        send_email_async_task(subject, emails, text_content='', html_content=html)
        total += len(emails)
    messages.success(request, f'{total} reminder emails sent.')

@admin.action(description='Send warning email to presentation participants')
def send_presentation_warning(modeladmin, request, queryset):
    total = 0
    for pres in queryset:
        qs = pres.enrollments.filter(status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE)
        emails = list(qs.values_list('user__email', flat=True))
        if not emails:
            continue
        html = render_to_string('warning.html', {
            'object': pres,
            'object_datetime': _format_datetime(pres.start_time),
            'object_location': pres.online_link if pres.is_online else pres.location or '',
        })
        subject = f'یادآوری ارائه: {pres.title}'
        send_email_async_task(subject, emails, text_content='', html_content=html)
        total += len(emails)
    messages.success(request, f'{total} reminder emails sent.')


@admin.action(description='Send reminder email to solo competition participants')
def send_solo_competition_reminder(modeladmin, request, queryset):
    total = 0
    for comp in queryset:
        qs = comp.registrations.filter(status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE)
        emails = list(qs.values_list('user__email', flat=True))
        if not emails:
            continue
        html = render_to_string('reminder.html', {
            'object': comp,
            'object_datetime': _format_datetime(comp.start_datetime),
        })
        subject = f'یادآوری مسابقهٔ تکی: {comp.title}'
        send_email_async_task(subject, emails, text_content='', html_content=html)
        total += len(emails)
    messages.success(request, f'{total} emails sent.')


@admin.action(description='Export solo competition participants to Excel (.xlsx)')
def export_solo_competition_registrations(modeladmin, request, queryset):
    registrations = SoloCompetitionRegistration.objects.filter(
        solo_competition__in=queryset,
        status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
    ).select_related('user', 'solo_competition')

    users_map = {}
    for reg in registrations:
        user = reg.user
        if not user:
            continue

        user_info = users_map.setdefault(user.id, {
            'full_name': user.get_full_name() or user.email,
            'email': user.email,
            'phone_number': getattr(user, 'phone_number', ''),
            'competitions': set()
        })
        user_info['competitions'].add(reg.solo_competition.title)

    if not users_map:
        modeladmin.message_user(request, "No completed registrations found for the selected competitions.",
                                level=messages.WARNING)
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Participants"
    headers = ["full_name", "email", "phone_number", "solo_competitions_owned"]
    ws.append(headers)

    for user_id, info in users_map.items():
        ws.append([
            info['full_name'],
            info['email'],
            info['phone_number'],
            ", ".join(sorted(info['competitions']))
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"solo_competition_participants_{ts}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin.action(description='Send reminder email to group competition participants')
def send_group_competition_reminder(modeladmin, request, queryset):
    total = 0
    for comp in queryset:
        registrations = comp.registrations.filter(status=CompetitionTeamRegistration.ACTIVE).select_related('team__leader').prefetch_related('members__user')
        emails_set = set()
        for registration in registrations:
            team = registration.team
            if team.leader and team.leader.email:
                emails_set.add(team.leader.email)
            member_emails = [member.user.email for member in registration.members.all()]
            emails_set.update(member_emails)
        if not emails_set:
            continue
        html = render_to_string('reminder.html', {
            'object': comp,
            'object_datetime': _format_datetime(comp.start_datetime),
        })
        subject = f'یادآوری مسابقهٔ گروهی: {comp.title}'
        send_email_async_task(subject, list(emails_set), text_content='', html_content=html)
        total += len(emails_set)
    messages.success(request, f'{total} emails sent.')


@admin.action(description='Export group competition teams and members to Excel (.xlsx)')
def export_group_competition_teams(modeladmin, request, queryset):
    registrations = CompetitionTeamRegistration.objects.filter(
        competition__in=queryset, status=CompetitionTeamRegistration.ACTIVE,
    ).select_related('team', 'competition').prefetch_related('members__user')
    users_map = {}
    for registration in registrations:
        for membership in registration.members.all():
            user = membership.user
            user_info = users_map.setdefault(user.id, {
                'full_name': user.get_full_name() or user.email,
                'email': user.email,
                'phone_number': getattr(user, 'phone_number', ''),
                'teams': set(), 'competitions': set(),
            })
            user_info['teams'].add(registration.team.name)
            user_info['competitions'].add(registration.competition.title)

    if not users_map:
        modeladmin.message_user(request, "No active team members found for the selected competitions.", level=messages.WARNING)
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Participants"
    headers = ["full_name", "email", "phone_number", "team_names", "group_competitions_attended"]
    ws.append(headers)

    for user_id, info in users_map.items():
        ws.append([
            info['full_name'],
            info['email'],
            info['phone_number'],
            ", ".join(sorted(info['teams'])),
            ", ".join(sorted(info['competitions']))
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"group_competition_participants_{ts}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin.register(Presenter)
class PresenterAdmin(admin.ModelAdmin):
    list_display = ("name", "email")
    search_fields = ("name", "email", "bio")


class SMSForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4, "cols": 50}), label="SMS Message")


class RegistrationPrerequisiteAdminForm(forms.ModelForm):
    required_item = forms.ChoiceField(
        label='Required prior registration',
        help_text='Choose one presentation, workshop, solo competition, or group competition.',
    )

    item_models = (
        ('presentation', Presentation, 'Presentations and workshops'),
        ('solo_competition', SoloCompetition, 'Solo competitions'),
        ('group_competition', GroupCompetition, 'Group competitions'),
    )

    class Meta:
        model = RegistrationPrerequisite
        fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [('', '---------')]
        for kind, model, label in self.item_models:
            choices.append((label, [
                (f'{kind}:{pk}', title)
                for pk, title in model.objects.order_by('title', 'pk').values_list('pk', 'title')
            ]))
            selected_id = getattr(self.instance, f'required_{kind}_id')
            if selected_id is not None:
                self.initial['required_item'] = f'{kind}:{selected_id}'
        self.fields['required_item'].choices = choices

    def clean(self):
        cleaned = super().clean()
        selection = cleaned.get('required_item')
        if selection:
            kind, object_id = selection.split(':', 1)
            model = next(model for candidate, model, _label in self.item_models if candidate == kind)
            try:
                item = model.objects.get(pk=object_id)
            except model.DoesNotExist:
                self.add_error('required_item', 'The selected item no longer exists.')
                return cleaned
            for candidate, _model, _label in self.item_models:
                setattr(self.instance, f'required_{candidate}', None)
            setattr(self.instance, f'required_{kind}', item)
        return cleaned


class RegistrationPrerequisiteInline(admin.TabularInline):
    model = RegistrationPrerequisite
    form = RegistrationPrerequisiteAdminForm
    extra = 0
    fields = ('required_item',)
    verbose_name = 'Required prior registration'
    verbose_name_plural = 'Required prior registrations (all must be completed)'


class PresentationPrerequisiteInline(RegistrationPrerequisiteInline):
    fk_name = 'presentation'


class SoloCompetitionPrerequisiteInline(RegistrationPrerequisiteInline):
    fk_name = 'solo_competition'


class GroupCompetitionPrerequisiteInline(RegistrationPrerequisiteInline):
    fk_name = 'group_competition'


@admin.register(Presentation)
class PresentationAdmin(admin.ModelAdmin):
    inlines = [PresentationPrerequisiteInline]
    list_display = ("title", "event", "type", "level", "start_time", "is_active", "is_paid")
    list_filter = ("is_active", "is_paid", "event", "type", "level")
    search_fields = ("title", "description", "event__title", "presenters__name")
    autocomplete_fields = ['event', 'presenters']
    filter_horizontal = ('presenters',)
    readonly_fields = ("poster_preview", "created_at")
    actions = [send_presentation_reminder, export_presentation_enrollments, send_presentation_warning, 'send_sms_to_participants']
    fieldsets = (
        (None, {
            "fields": (
                "event", "title", "description", "presenters",
                "type", "level", "is_online", "location", "online_link",
                "start_time", "end_time", "is_active", "requirements",
                # "contents", "timing"
            )
        }),
        ("Payment", {"fields": ("is_paid", "price", "capacity")}),
        ("Media",   {"fields": ("poster", "poster_preview")}),
        ("Meta",    {"fields": ("created_at",)}),
    )

    @admin.action(description="Send SMS to Completed/Free Enrolled Users")
    def send_sms_to_participants(self, request, queryset):
        selected_ids = request.POST.getlist('_selected_action') or queryset.values_list('id', flat=True)
        presentations = Presentation.objects.filter(pk__in=selected_ids)

        if request.method == "POST" and "apply" in request.POST:
            form = SMSForm(request.POST)
            if form.is_valid():
                message_text = form.cleaned_data["message"]
                total_sent = 0
                for pres in presentations:
                    for enrollment in pres.enrollments.filter(status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).select_related('user'):
                        phone = getattr(enrollment.user, "phone_number", None)
                        if phone:
                            print(f"Sending SMS to {phone}")
                            send_sms(phone, message_text)
                            total_sent += 1
                self.message_user(request, f"SMS sent to {total_sent} users.")
                return None
        else:
            form = SMSForm(initial={"_selected_action": selected_ids})

        return render(
            request,
            "send_sms.html",
            {
                "form": form,
                "queryset": presentations,
                "action": "send_sms_to_participants",
                "title": "Send SMS to Completed/Free Enrolled Users",
            },
        )


    @admin.display(description="Poster Preview")
    def poster_preview(self, obj):
        if obj.poster and hasattr(obj.poster, "url"):
            return format_html('<img src="{}" style="max-width:320px;height:auto;border-radius:6px;" />', obj.poster.url)
        return "No poster uploaded"


class PresentationInline(admin.TabularInline):
    model = Presentation
    extra = 0
    fields = ('title', 'type', 'start_time', 'end_time', 'is_active')
    show_change_link = True
    ordering = ('start_time',)


class SoloCompetitionInline(admin.TabularInline):
    model = SoloCompetition
    extra = 0
    fields = ('title', 'start_datetime', 'end_datetime', 'is_active')
    show_change_link = True
    ordering = ('start_datetime',)


class GroupCompetitionInline(admin.TabularInline):
    model = GroupCompetition
    extra = 0
    fields = ('title', 'start_datetime', 'end_datetime', 'is_active')
    show_change_link = True
    ordering = ('start_datetime',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'start_date', 'end_date', 'is_active')
    search_fields = ('id', 'title', 'description')
    list_filter = ('is_active', 'start_date')
    inlines = [PresentationInline, SoloCompetitionInline, GroupCompetitionInline]
    readonly_fields = ('created_at',)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if obj:
            readonly_fields.append('id')
        return readonly_fields


@admin.register(SoloCompetition)
class SoloCompetitionAdmin(admin.ModelAdmin):
    inlines = [SoloCompetitionPrerequisiteInline]
    list_display = ('title', 'event', 'start_datetime', 'is_active', 'is_paid')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('is_paid', 'is_active', 'event')
    autocomplete_fields = ['event']
    readonly_fields = ('created_at',)
    actions = [send_solo_competition_reminder, export_solo_competition_registrations]


@admin.register(GroupCompetition)
class GroupCompetitionAdmin(admin.ModelAdmin):
    inlines = [GroupCompetitionPrerequisiteInline]
    list_display = ('title', 'event', 'start_datetime', 'is_active', 'requires_admin_approval')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('is_paid', 'is_active', 'requires_admin_approval', 'event')
    autocomplete_fields = ['event']
    readonly_fields = ('created_at',)
    actions = [send_group_competition_reminder, export_group_competition_teams]


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    form = TeamMembershipAdminForm
    formset = TeamMembershipInlineFormSet
    extra = 0
    autocomplete_fields = ('user',)
    readonly_fields = ('joined_at', 'invited_by', 'responded_at')

    def has_delete_permission(self, request, obj=None):
        return False
    ordering = ('-joined_at',)
    fields = ('user', 'status', 'joined_at', 'invited_by', 'responded_at')


class ContentImageInline(admin.TabularInline):
    model = ContentImage
    extra = 1

class TeamContentInline(admin.StackedInline):
    model = TeamContent
    extra = 0
    readonly_fields = ('created_at',)
    inlines = [ContentImageInline]


@admin.register(CompetitionTeam)
class CompetitionTeamAdmin(admin.ModelAdmin):
    form = CompetitionTeamAdminForm
    list_display = ('name', 'leader', 'group_competition', 'status', 'is_approved_by_admin')
    search_fields = ('name', 'leader__email', 'group_competition__title')
    list_filter = ('status', 'is_approved_by_admin', 'group_competition__event')
    autocomplete_fields = ['group_competition']
    inlines = [TeamMembershipInline, TeamContentInline]
    readonly_fields = ('created_at', 'group_competition', 'status', 'is_approved_by_admin', 'admin_remarks')
    list_select_related = ('leader', 'group_competition')

    def has_add_permission(self, request):
        return False

    def save_formset(self, request, form, formset, change):
        if formset.model is not TeamMembership:
            return super().save_formset(request, form, formset, change)
        memberships = formset.save(commit=False)
        for membership in memberships:
            if membership.pk is None:
                membership.invited_by = request.user
            membership.save()
        formset.save_m2m()

    def has_delete_permission(self, request, obj=None):
        return bool(obj and not obj.registrations.exists() and not obj.group_competition_id) and super().has_delete_permission(request, obj)

    def _review(self, request, queryset, approve):
        from .services import CompetitionError, legacy_registration, review_registration
        updated = 0
        for team in queryset:
            try:
                registration = legacy_registration(team)
                review_registration(registration.pk, request.user, approve=approve, remarks=team.admin_remarks or '')
                updated += 1
            except CompetitionError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
        self.message_user(request, f'{updated} registration(s) reviewed.')

    @admin.action(description='Approve selected teams (single registration only)')
    def approve_teams(self, request, queryset):
        self._review(request, queryset, True)

    @admin.action(description='Reject selected teams (single registration only)')
    def reject_teams(self, request, queryset):
        self._review(request, queryset, False)

    actions = ['approve_teams', 'reject_teams']


@admin.register(CompetitionTeamRegistration)
class CompetitionTeamRegistrationAdmin(admin.ModelAdmin):
    list_display = ('team', 'competition', 'status', 'price', 'reviewed_by', 'reviewed_at', 'activated_at')
    list_filter = ('status', 'competition__event')
    search_fields = ('team__name', 'team__leader__email', 'competition__title')
    list_select_related = ('team', 'competition', 'reviewed_by')
    readonly_fields = ('team', 'competition', 'status', 'price', 'order_item', 'reviewed_by',
                       'reviewed_at', 'activated_at', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status not in (CompetitionTeamRegistration.PENDING_APPROVAL,
                                      CompetitionTeamRegistration.PENDING_PAYMENT):
            return (*self.readonly_fields, 'admin_remarks')
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def _review(self, request, queryset, approve):
        from .services import CompetitionError, review_registration
        updated = 0
        for registration in queryset:
            try:
                review_registration(registration.pk, request.user, approve=approve, remarks=registration.admin_remarks)
                updated += 1
            except CompetitionError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
        self.message_user(request, f'{updated} registration(s) reviewed.')

    @admin.action(description='Approve selected registrations')
    def approve(self, request, queryset):
        self._review(request, queryset, True)

    @admin.action(description='Reject selected registrations')
    def reject(self, request, queryset):
        self._review(request, queryset, False)

    actions = ['approve', 'reject']


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    form = TeamMembershipAdminForm
    list_display = ('user', 'team', 'status', 'joined_at')
    search_fields = ('user__email', 'team__name')
    list_filter = ('status', 'team__group_competition')
    autocomplete_fields = ['user', 'team']
    readonly_fields = ('joined_at', 'invited_by', 'expires_at', 'responded_at')
    list_select_related = ('user', 'team')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.invited_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PresentationEnrollment)
class PresentationEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'presentation', 'status', 'enrolled_at')
    search_fields = ('user__email', 'presentation__title')
    list_filter = ('status', 'presentation__event')
    autocomplete_fields = ['user', 'presentation', 'order_item']
    readonly_fields = ('enrolled_at',)
    list_select_related = ('user', 'presentation')
    actions = ('revoke_enrollments',)

    @admin.action(description='Revoke selected presentation enrollments')
    def revoke_enrollments(self, request, queryset):
        enrollments = list(queryset.exclude(status=PresentationEnrollment.STATUS_CANCELLED))
        if not enrollments:
            self.message_user(request, 'The selected enrollments are already revoked.', messages.INFO)
            return

        PresentationEnrollment.objects.filter(
            pk__in=[enrollment.pk for enrollment in enrollments]
        ).update(status=PresentationEnrollment.STATUS_CANCELLED)

        for enrollment in enrollments:
            enrollment.status = PresentationEnrollment.STATUS_CANCELLED
            self.log_change(request, enrollment, 'Enrollment revoked.')

        self.message_user(
            request,
            f'{len(enrollments)} presentation enrollment(s) revoked.',
            messages.SUCCESS,
        )

    def has_delete_permission(self, request, obj=None):
        # Regular staff should revoke access through the explicit action so paid
        # order history remains linked to a durable enrollment record. Superusers
        # may still explicitly purge test data after reviewing Django's cascade
        # deletion confirmation page.
        return request.user.is_superuser


@admin.register(SoloCompetitionRegistration)
class SoloCompetitionRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'solo_competition', 'status', 'registered_at')
    search_fields = ('user__email', 'solo_competition__title')
    list_filter = ('status', 'solo_competition__event')
    autocomplete_fields = ['user', 'solo_competition', 'order_item']
    readonly_fields = ('registered_at',)
    list_select_related = ('user', 'solo_competition')


@admin.register(TeamContent)
class TeamContentAdmin(admin.ModelAdmin):
    list_display = ('team', 'file_link', 'created_at')
    search_fields = ('team__name',)
    autocomplete_fields = ['team']
    inlines = [ContentImageInline]
    readonly_fields = ('created_at', 'team', 'registration')

    def has_add_permission(self, request):
        return False

@admin.register(ContentLike)
class ContentLikeAdmin(admin.ModelAdmin):
    list_display = ('user', 'team_content', 'created_at')
    search_fields = ('user__email', 'team_content__team__name')
    autocomplete_fields = ['user', 'team_content']


@admin.register(ContentComment)
class ContentCommentAdmin(admin.ModelAdmin):
    list_display = ('user', 'text_snippet', 'created_at')
    search_fields = ('user__email', 'text')
    autocomplete_fields = ['user', 'team_content']

    @admin.display(description="Comment")
    def text_snippet(self, obj):
        return (obj.text[:75] + '...') if len(obj.text) > 75 else obj.text


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "published_at")
    list_filter = ("is_active",)
    search_fields = ("title", "excerpt", "body_markdown")
    ordering = ("-published_at",)
