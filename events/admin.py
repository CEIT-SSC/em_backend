from django.contrib import admin
from .models import (
    Presenter, Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    TeamContent, ContentImage, ContentLike, ContentComment, # New models
    PresentationEnrollment, SoloCompetitionRegistration
)

@admin.register(Presenter)
class PresenterAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'created_at',  )
    search_fields = ('name', 'email')
    list_filter = ('created_at',)

class PresentationInline(admin.TabularInline):
    model = Presentation
    extra = 1
    autocomplete_fields = ['presenters']

class SoloCompetitionInline(admin.TabularInline):
    model = SoloCompetition
    extra = 0

class GroupCompetitionInline(admin.TabularInline):
    model = GroupCompetition
    extra = 0

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'start_date', 'end_date', 'is_active', 'created_at',  )
    search_fields = ('title', 'description')
    list_filter = ('is_active', 'start_date', 'end_date')
    inlines = [PresentationInline, SoloCompetitionInline, GroupCompetitionInline]
    readonly_fields = ('created_at',  )


@admin.register(Presentation)
class PresentationAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'type', 'start_time', 'end_time', 'is_paid', 'price', 'capacity')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('type', 'is_online', 'is_paid', 'event')
    autocomplete_fields = ['presenters', 'event']
    readonly_fields = ('created_at',  )

@admin.register(SoloCompetition)
class SoloCompetitionAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'start_datetime', 'end_datetime', 'is_paid', 'price_per_participant', 'max_participants')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('is_paid', 'event', 'start_datetime')
    autocomplete_fields = ['event']
    readonly_fields = ('created_at',  )

class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 1
    autocomplete_fields = ['user']
    readonly_fields = ('joined_at',)

class ContentImageInline(admin.TabularInline):
    model = ContentImage
    extra = 1
    readonly_fields = ('uploaded_at',)

@admin.register(TeamContent)
class TeamContentAdmin(admin.ModelAdmin):
    list_display = ('team', 'description_snippet', 'file_link', 'created_at',  )
    search_fields = ('team__name', 'team__group_competition__title', 'description')
    list_filter = ('team__group_competition', 'created_at')
    autocomplete_fields = ['team']
    inlines = [ContentImageInline]
    readonly_fields = ('created_at',  )

    def description_snippet(self, obj):
        return obj.description[:50] + "..." if len(obj.description) > 50 else obj.description
    description_snippet.short_description = "Description"


class TeamContentInline(admin.StackedInline):
    model = TeamContent
    extra = 0
    can_delete = True
    show_change_link = True
    inlines = [ContentImageInline]
    readonly_fields = ('created_at',  )


@admin.register(CompetitionTeam)
class CompetitionTeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'leader', 'group_competition', 'status', 'is_approved_by_admin', 'created_at',  )
    search_fields = ('name', 'leader__email', 'group_competition__title')
    list_filter = ('status', 'is_approved_by_admin', 'group_competition')
    autocomplete_fields = ['leader', 'group_competition']
    inlines = [TeamMembershipInline, TeamContentInline] # Added TeamContentInline
    actions = ['approve_teams', 'reject_teams']
    readonly_fields = ('created_at',  )


    def approve_teams(self, request, queryset):
        updated_count = 0
        for team in queryset:
            if team.needs_admin_approval() and team.status == CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION:
                team.is_approved_by_admin = True
                if not team.group_competition.is_paid or (team.group_competition.price_per_group is not None and team.group_competition.price_per_group <= 0):
                    team.status = CompetitionTeam.STATUS_ACTIVE
                else:
                    team.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                team.save()
                updated_count += 1
        self.message_user(request, f"{updated_count} team(s) successfully approved.")
    approve_teams.short_description = "Approve selected teams"

    def reject_teams(self, request, queryset):
        updated_count = 0
        for team in queryset:
            if team.needs_admin_approval() and team.status == CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION:
                team.is_approved_by_admin = False
                team.status = CompetitionTeam.STATUS_REJECTED_BY_ADMIN
                team.save()
                updated_count += 1
        self.message_user(request, f"{updated_count} team(s) successfully rejected.")
    reject_teams.short_description = "Reject selected teams"


@admin.register(GroupCompetition)
class GroupCompetitionAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'start_datetime', 'is_paid', 'price_per_group', 'requires_admin_approval', 'allow_content_submission')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('is_paid', 'requires_admin_approval', 'allow_content_submission', 'event', 'start_datetime')
    autocomplete_fields = ['event']
    readonly_fields = ('created_at',  )


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'team', 'joined_at')
    search_fields = ('user__email', 'team__name')
    list_filter = ('team__group_competition',)
    autocomplete_fields = ['user', 'team']
    readonly_fields = ('joined_at',)

@admin.register(PresentationEnrollment)
class PresentationEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'presentation', 'status', 'enrolled_at')
    search_fields = ('user__email', 'presentation__title')
    list_filter = ('status', 'presentation')
    autocomplete_fields = ['user', 'presentation', 'order_item']
    readonly_fields = ('enrolled_at',)

@admin.register(SoloCompetitionRegistration)
class SoloCompetitionRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'solo_competition', 'status', 'registered_at')
    search_fields = ('user__email', 'solo_competition__title')
    list_filter = ('status', 'solo_competition')
    autocomplete_fields = ['user', 'solo_competition', 'order_item']
    readonly_fields = ('registered_at',)

@admin.register(ContentImage)
class ContentImageAdmin(admin.ModelAdmin):
    list_display = ('team_content_info', 'image', 'caption', 'uploaded_at')
    search_fields = ('team_content__team__name', 'caption')
    list_filter = ('team_content__team__group_competition',)
    autocomplete_fields = ['team_content']
    readonly_fields = ('uploaded_at',)

    def team_content_info(self, obj):
        return str(obj.team_content)
    team_content_info.short_description = "Team Content"

@admin.register(ContentLike)
class ContentLikeAdmin(admin.ModelAdmin):
    list_display = ('user', 'team_content_info', 'created_at')
    search_fields = ('user__email', 'team_content__team__name')
    list_filter = ('team_content__team__group_competition', 'created_at')
    autocomplete_fields = ['user', 'team_content']
    readonly_fields = ('created_at',)

    def team_content_info(self, obj):
        return str(obj.team_content)
    team_content_info.short_description = "Liked Content"

@admin.register(ContentComment)
class ContentCommentAdmin(admin.ModelAdmin):
    list_display = ('user', 'team_content_info', 'text_snippet', 'created_at',  )
    search_fields = ('user__email', 'team_content__team__name', 'text')
    list_filter = ('team_content__team__group_competition', 'created_at')
    autocomplete_fields = ['user', 'team_content']
    readonly_fields = ('created_at',  )

    def team_content_info(self, obj):
        return str(obj.team_content)
    team_content_info.short_description = "Commented Content"

    def text_snippet(self, obj):
        return obj.text[:50] + "..." if len(obj.text) > 50 else obj.text
    text_snippet.short_description = "Comment"
