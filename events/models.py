from django.db import models
from django.conf import settings

class Presenter(models.Model):
    name = models.CharField(max_length=255, verbose_name="Full Name")
    email = models.EmailField(blank=True, null=True, verbose_name="Public Contact Email")
    bio = models.TextField(blank=True, null=True, verbose_name="Biography")
    presenter_picture = models.ImageField(upload_to='presenter_pics/%Y/%m/', blank=True, null=True, verbose_name="Presenter Picture")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Presenter"
        verbose_name_plural = "Presenters"
        ordering = ['name']

class Event(models.Model):
    id = models.IntegerField(primary_key=True, verbose_name="Event ID")
    title = models.CharField(max_length=255, verbose_name="Event Title")
    description = models.TextField(verbose_name="Event Description")
    start_date = models.DateTimeField(verbose_name="Start Date & Time")
    end_date = models.DateTimeField(verbose_name="End Date & Time")
    is_active = models.BooleanField(default=False, verbose_name="Is Event Active?")
    created_at = models.DateTimeField(auto_now_add=True)
    poster = models.ImageField(upload_to='event_posters/%Y/%m/', blank=True, null=True, verbose_name="Event Poster")
    landing_url = models.URLField(max_length=500, blank=True, null=True, verbose_name="Page URL")
    manager = models.CharField(max_length=255, verbose_name="Manager Name")

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Event"
        verbose_name_plural = "Events"
        ordering = ['-start_date', 'title']

class Presentation(models.Model):
    COURSE = "course"
    TALK = "talk"
    WORKSHOP = "workshop"
    PRESENTATION_TYPE_CHOICES = [(COURSE, "course"), (TALK, "Talk"), (WORKSHOP, "Workshop")]

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    LEVEL_CHOICES = [
        (BEGINNER, "Beginner"),
        (INTERMEDIATE, "Intermediate"),
        (ADVANCED, "Advanced"),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="presentations",
                              verbose_name="Parent Event", blank=True, null=True)
    title = models.CharField(max_length=255, verbose_name="Presentation Title")
    description = models.TextField(verbose_name="Presentation Description")
    presenters = models.ManyToManyField(Presenter, blank=True, related_name="presentations", verbose_name="Presenters")
    type = models.CharField(max_length=10, choices=PRESENTATION_TYPE_CHOICES, default=TALK, verbose_name="Type")
    level = models.CharField(max_length=12, choices=LEVEL_CHOICES, default=BEGINNER, verbose_name="Level")
    is_online = models.BooleanField(default=False, verbose_name="Is Online?")
    location = models.CharField(max_length=255, blank=True, null=True, verbose_name="Location (if offline)")
    online_link = models.URLField(blank=True, null=True, verbose_name="Online Link (if online)")
    start_time = models.DateTimeField(verbose_name="Start Time")
    end_time = models.DateTimeField(verbose_name="End Time")
    is_paid = models.BooleanField(default=False, verbose_name="Is Paid?")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, blank=True, null=True, verbose_name="Price (e.g., Toman)")
    capacity = models.PositiveIntegerField(blank=True, null=True, verbose_name="Capacity")
    is_active = models.BooleanField(default=True, verbose_name="Is Active for Registration?")
    poster = models.ImageField(upload_to='presentation_posters/%Y/%m/', blank=True, null=True, verbose_name="Presentation Poster")
    created_at = models.DateTimeField(auto_now_add=True)
    requirements = models.TextField(
        blank=True,
        default="",
        verbose_name="Requirements",
        help_text="Prerequisites or materials participants should have/know."
    )

    def __str__(self):
        return f"{self.title}"

    class Meta:
        verbose_name = "Presentation"
        verbose_name_plural = "Presentations"
        ordering = ['start_time', 'title']

class BaseCompetition(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, verbose_name="Parent Event", blank=True, null=True)
    title = models.CharField(max_length=255, verbose_name="Competition Title")
    description = models.TextField(verbose_name="Description")
    start_datetime = models.DateTimeField(verbose_name="Start Date & Time")
    end_datetime = models.DateTimeField(verbose_name="End Date & Time")
    rules = models.TextField(blank=True, null=True, verbose_name="Rules")
    is_paid = models.BooleanField(default=False, verbose_name="Is Paid?")
    prize_details = models.TextField(blank=True, null=True, verbose_name="Prize Details")
    is_active = models.BooleanField(default=True, verbose_name="Is Active for Registration?")
    created_at = models.DateTimeField(auto_now_add=True)
    poster = models.ImageField(upload_to='competition_posters/%Y/%m/', blank=True, null=True, verbose_name="Competition Poster")

    class Meta:
        abstract = True
        ordering = ['start_datetime', 'title']

    def __str__(self):
        return f"{self.title}"

class SoloCompetition(BaseCompetition):
    price_per_participant = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, blank=True, null=True, verbose_name="Price per Participant (e.g., Toman)")
    max_participants = models.PositiveIntegerField(blank=True, null=True, verbose_name="Max Participants")

    class Meta:
        verbose_name = "Solo Competition"
        verbose_name_plural = "Solo Competitions"

class GroupCompetition(BaseCompetition):
    price_per_member = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, blank=True, null=True, verbose_name="Price per Member (e.g., Toman)")
    min_group_size = models.PositiveIntegerField(default=1, verbose_name="Min Group Size")
    max_group_size = models.PositiveIntegerField(verbose_name="Max Group Size")
    max_teams = models.PositiveIntegerField(blank=True, null=True, verbose_name="Max Teams")
    requires_admin_approval = models.BooleanField(default=False, verbose_name="Requires Admin Approval for Teams?")
    member_verification_instructions = models.TextField(blank=True, null=True, verbose_name="Member Verification Instructions")
    allow_content_submission = models.BooleanField(default=False, verbose_name="Allow Teams to Submit Content?")

    class Meta:
        verbose_name = "Group Competition"
        verbose_name_plural = "Group Competitions"

class CompetitionTeam(models.Model):
    STATUS_PENDING_ADMIN_VERIFICATION = "pending_admin_verification"
    STATUS_REJECTED_BY_ADMIN = "rejected_by_admin"
    STATUS_APPROVED_AWAITING_PAYMENT = "approved_awaiting_payment"
    STATUS_IN_CART = "in_cart"
    STATUS_AWAITING_PAYMENT_CONFIRMATION = "awaiting_payment_confirmation"
    STATUS_PAYMENT_FAILED = "payment_failed"
    STATUS_ACTIVE = "active"
    STATUS_CANCELLED = "cancelled"

    TEAM_STATUS_CHOICES = [
        (STATUS_PENDING_ADMIN_VERIFICATION, "Pending Admin Verification"),
        (STATUS_REJECTED_BY_ADMIN, "Rejected by Admin"),
        (STATUS_APPROVED_AWAITING_PAYMENT, "Approved - Awaiting Payment"),
        (STATUS_IN_CART, "In Cart (Awaiting Checkout)"),
        (STATUS_AWAITING_PAYMENT_CONFIRMATION, "Awaiting Payment Confirmation"),
        (STATUS_PAYMENT_FAILED, "Payment Failed"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    name = models.CharField(max_length=255, verbose_name="Team Name")
    leader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="led_teams", verbose_name="Team Leader")
    group_competition = models.ForeignKey(GroupCompetition, on_delete=models.CASCADE, related_name="teams", verbose_name="Parent Group Competition")
    status = models.CharField(max_length=40, choices=TEAM_STATUS_CHOICES, default=STATUS_IN_CART, verbose_name="Team Status")
    is_approved_by_admin = models.BooleanField(default=False, verbose_name="Has Admin Approved?")
    admin_remarks = models.TextField(blank=True, null=True, verbose_name="Admin Remarks")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} for {self.group_competition.title} (Leader: {self.leader.email})"

    class Meta:
        verbose_name = "Competition Team"
        verbose_name_plural = "Competition Teams"
        unique_together = ('group_competition', 'name')
        ordering = ['-created_at', 'name']

    def needs_admin_approval(self):
        return self.group_competition.requires_admin_approval

class TeamMembership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships", verbose_name="User")
    team = models.ForeignKey(CompetitionTeam, on_delete=models.CASCADE, related_name="memberships", verbose_name="Team")
    government_id_picture = models.ImageField(upload_to='gov_ids/%Y/%m/', blank=True, null=True, verbose_name="Government ID Picture")
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} in {self.team.name}"

    class Meta:
        verbose_name = "Team Membership"
        verbose_name_plural = "Team Memberships"
        unique_together = ('user', 'team')
        ordering = ['team', 'user__email']

class TeamContent(models.Model):
    team = models.OneToOneField(CompetitionTeam, on_delete=models.CASCADE, related_name="content_submission", verbose_name="Team")
    description = models.TextField(verbose_name="Content Description")
    file_link = models.URLField(max_length=500, blank=True, null=True, verbose_name="Link to External File/Repository")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Content for Team: {self.team.name} in {self.team.group_competition.title}"

    class Meta:
        verbose_name = "Team Content Submission"
        verbose_name_plural = "Team Content Submissions"
        ordering = ['-created_at']

def team_content_image_path(instance, filename):
    return f'team_content_images/{instance.team_content.team.id}/{filename}'

class ContentImage(models.Model):
    team_content = models.ForeignKey(TeamContent, on_delete=models.CASCADE, related_name="images", verbose_name="Team Content")
    image = models.ImageField(upload_to=team_content_image_path, verbose_name="Image")
    caption = models.CharField(max_length=255, blank=True, null=True, verbose_name="Caption")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.team_content.team.name}'s content (ID: {self.id})"

    class Meta:
        verbose_name = "Content Image"
        verbose_name_plural = "Content Images"
        ordering = ['uploaded_at']

class ContentLike(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="content_likes", verbose_name="User")
    team_content = models.ForeignKey(TeamContent, on_delete=models.CASCADE, related_name="likes", verbose_name="Liked Content")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} likes content by {self.team_content.team.name}"

    class Meta:
        verbose_name = "Content Like"
        verbose_name_plural = "Content Likes"
        unique_together = ('user', 'team_content')
        ordering = ['-created_at']

class ContentComment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="content_comments", verbose_name="User")
    team_content = models.ForeignKey(TeamContent, on_delete=models.CASCADE, related_name="comments", verbose_name="Commented Content")
    text = models.TextField(verbose_name="Comment Text")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Comment by {self.user.email} on content by {self.team_content.team.name} (ID: {self.id})"

    class Meta:
        verbose_name = "Content Comment"
        verbose_name_plural = "Content Comments"
        ordering = ['created_at']

class PresentationEnrollment(models.Model):
    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_COMPLETED_OR_FREE = "completed_or_free"
    STATUS_PAYMENT_FAILED = "payment_failed"
    STATUS_CANCELLED = "cancelled"
    ENROLLMENT_STATUS_CHOICES = [
        (STATUS_PENDING_PAYMENT, "Pending Payment"),
        (STATUS_COMPLETED_OR_FREE, "Completed/Free"),
        (STATUS_PAYMENT_FAILED, "Payment Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="presentation_enrollments")
    presentation = models.ForeignKey(Presentation, on_delete=models.CASCADE, related_name="enrollments")
    order_item = models.OneToOneField('shop.OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name="presentation_enrollment_record_link")
    status = models.CharField(max_length=20, choices=ENROLLMENT_STATUS_CHOICES, default=STATUS_PENDING_PAYMENT, verbose_name="Enrollment Status")
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'presentation')
        verbose_name = "Presentation Enrollment"
        verbose_name_plural = "Presentation Enrollments"

    def __str__(self):
        return f"{self.user.email} enrolled in {self.presentation.title} ({self.get_status_display()})"

class SoloCompetitionRegistration(models.Model):

    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_COMPLETED_OR_FREE = "completed_or_free"
    STATUS_PAYMENT_FAILED = "payment_failed"
    STATUS_CANCELLED = "cancelled"

    REGISTRATION_STATUS_CHOICES = [
        (STATUS_PENDING_PAYMENT, "Pending Payment"),
        (STATUS_COMPLETED_OR_FREE, "Completed/Free"),
        (STATUS_PAYMENT_FAILED, "Payment Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="solo_competition_registrations"
    )
    solo_competition = models.ForeignKey(
        "SoloCompetition",
        on_delete=models.CASCADE,
        related_name="registrations"
    )
    order_item = models.OneToOneField(
        "shop.OrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solo_registration_record_link"
    )
    status = models.CharField(
        max_length=20,
        choices=REGISTRATION_STATUS_CHOICES,
        default=STATUS_PENDING_PAYMENT,
        verbose_name="Registration Status"
    )
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'solo_competition')
        verbose_name = "Solo Competition Registration"
        verbose_name_plural = "Solo Competition Registrations"

    def __str__(self):
        return f"{self.user.email} registered for {self.solo_competition.title} ({self.get_status_display()})"
class Post(models.Model):
    title = models.CharField(max_length=255)
    excerpt = models.TextField(
        help_text="Short plain‐text summary shown in the feed"
    )
    body_markdown = models.TextField(
        help_text="Full post content in Markdown"
    )
    published_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title