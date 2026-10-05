from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms.models import inlineformset_factory
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from shop.eligibility import OrderPaymentEligibilityError, validate_order_items_for_payment
from shop.models import Cart, CartItem, Order, OrderItem, Pack, PackItem

from .models import (
    CompetitionTeam, CompetitionTeamRegistration, GroupCompetition,
    Presentation, PresentationEnrollment, RegistrationPrerequisite,
    SoloCompetition, SoloCompetitionRegistration, TeamMembership,
)
from .services import CompetitionError, register_free_solo, register_team
from .admin import RegistrationPrerequisiteAdminForm


class RegistrationPrerequisiteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='leader@example.com', is_active=True)
        self.member = get_user_model().objects.create_user(email='member@example.com', is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.start = timezone.now() + timedelta(days=2)

    def presentation(self, **kwargs):
        values = dict(title='Workshop', description='Test', start_time=self.start,
                      end_time=self.start + timedelta(hours=1))
        values.update(kwargs)
        return Presentation.objects.create(**values)

    def solo(self, **kwargs):
        return SoloCompetition.objects.create(
            title='Solo', description='Test', start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1), **kwargs,
        )

    def group(self, **kwargs):
        return GroupCompetition.objects.create(
            title='Group', description='Test', start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1), min_group_size=1,
            max_group_size=3, **kwargs,
        )

    def team(self, *users):
        team = CompetitionTeam.objects.create(name='Team', leader=users[0])
        for user in users:
            TeamMembership.objects.create(team=team, user=user, status=TeamMembership.STATUS_ACCEPTED)
        return team

    def test_admin_rule_validation(self):
        workshop = self.presentation()
        rule = RegistrationPrerequisite(presentation=workshop, required_presentation=workshop)
        with self.assertRaises(ValidationError):
            rule.full_clean()
        rule.required_presentation = None
        rule.required_group_competition = self.group()
        rule.full_clean()

        formset_class = inlineformset_factory(
            Presentation, RegistrationPrerequisite, fk_name='presentation',
            fields=('required_presentation', 'required_solo_competition', 'required_group_competition'),
            extra=1,
        )
        prefix = formset_class.get_default_prefix()
        formset = formset_class(instance=workshop, data={
            f'{prefix}-TOTAL_FORMS': '1', f'{prefix}-INITIAL_FORMS': '0',
            f'{prefix}-MIN_NUM_FORMS': '0', f'{prefix}-MAX_NUM_FORMS': '1000',
            f'{prefix}-0-required_presentation': '',
            f'{prefix}-0-required_solo_competition': '',
            f'{prefix}-0-required_group_competition': str(rule.required_group_competition_id),
        })
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_admin_can_add_prerequisite_with_new_unsaved_parent(self):
        required = self.presentation()
        for model, fk_name, parent in (
            (Presentation, 'presentation', Presentation(
                title='New workshop', description='Test', start_time=self.start, end_time=self.start)),
            (SoloCompetition, 'solo_competition', SoloCompetition(
                title='New solo', description='Test', start_datetime=self.start, end_datetime=self.start)),
            (GroupCompetition, 'group_competition', GroupCompetition(
                title='New group', description='Test', start_datetime=self.start,
                end_datetime=self.start, max_group_size=3)),
        ):
            with self.subTest(parent=model.__name__):
                formset_class = inlineformset_factory(
                    model, RegistrationPrerequisite, fk_name=fk_name,
                    form=RegistrationPrerequisiteAdminForm, fields=('required_item',), extra=0,
                )
                prefix = formset_class.get_default_prefix()
                formset = formset_class(instance=parent, data={
                    f'{prefix}-TOTAL_FORMS': '1', f'{prefix}-INITIAL_FORMS': '0',
                    f'{prefix}-MIN_NUM_FORMS': '0', f'{prefix}-MAX_NUM_FORMS': '1000',
                    f'{prefix}-0-required_item': f'presentation:{required.pk}',
                })
                self.assertTrue(formset.is_valid(), formset.errors)
                parent.save()
                rule = formset.save()[0]
                self.assertEqual(getattr(rule, f'{fk_name}_id'), parent.pk)
                self.assertEqual(rule.required_presentation_id, required.pk)
                self.assertEqual({field.name for field in formset.forms[0].visible_fields()},
                                 {'required_item', 'DELETE'})

    def test_admin_single_selector_edits_existing_prerequisite(self):
        target = self.group()
        presentation = self.presentation()
        solo = self.solo()
        rule = RegistrationPrerequisite.objects.create(
            group_competition=target, required_presentation=presentation,
        )
        form = RegistrationPrerequisiteAdminForm(instance=rule)
        self.assertEqual(form.initial['required_item'], f'presentation:{presentation.pk}')
        form = RegistrationPrerequisiteAdminForm(
            instance=rule, data={'required_item': f'solo_competition:{solo.pk}'},
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        rule.refresh_from_db()
        self.assertIsNone(rule.required_presentation_id)
        self.assertEqual(rule.required_solo_competition_id, solo.pk)

    def test_workshop_requires_active_team_registration_for_each_member(self):
        competition = self.group()
        workshop = self.presentation(is_paid=False)
        RegistrationPrerequisite.objects.create(
            presentation=workshop, required_group_competition=competition,
        )
        response = self.client.post('/api/cart/items/', {'item_type': 'presentation', 'item_id': workshop.pk})
        self.assertEqual(response.status_code, 400)

        team = self.team(self.user, self.member)
        register_team(team.pk, competition.pk, self.user)
        response = self.client.post('/api/cart/items/', {'item_type': 'presentation', 'item_id': workshop.pk})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(PresentationEnrollment.objects.filter(
            user=self.user, presentation=workshop,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        ).exists())

        other_workshop = self.presentation(title='Second workshop', is_paid=False)
        RegistrationPrerequisite.objects.create(
            presentation=other_workshop, required_group_competition=competition,
        )
        self.client.force_authenticate(self.member)
        response = self.client.post('/api/cart/items/', {'item_type': 'presentation', 'item_id': other_workshop.pk})
        self.assertEqual(response.status_code, 201)

    def test_team_registration_requires_every_member_to_have_prerequisite(self):
        target = self.group()
        prerequisite = self.solo()
        RegistrationPrerequisite.objects.create(
            group_competition=target, required_solo_competition=prerequisite,
        )
        team = self.team(self.user, self.member)
        SoloCompetitionRegistration.objects.create(
            user=self.user, solo_competition=prerequisite,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
        )
        with self.assertRaises(CompetitionError):
            register_team(team.pk, target.pk, self.user)
        SoloCompetitionRegistration.objects.create(
            user=self.member, solo_competition=prerequisite,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
        )
        self.assertEqual(register_team(team.pk, target.pk, self.user).status,
                         CompetitionTeamRegistration.ACTIVE)

    def test_solo_registration_requires_completed_presentation(self):
        target = self.solo()
        prerequisite = self.presentation()
        RegistrationPrerequisite.objects.create(
            solo_competition=target, required_presentation=prerequisite,
        )
        with self.assertRaises(CompetitionError):
            register_free_solo(target.pk, self.user)
        PresentationEnrollment.objects.create(
            user=self.user, presentation=prerequisite,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        )
        self.assertEqual(register_free_solo(target.pk, self.user).status,
                         SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE)

    def test_checkout_and_payment_recheck_prerequisite(self):
        target = self.presentation(is_paid=True, price=Decimal('100'))
        prerequisite = self.solo()
        RegistrationPrerequisite.objects.create(
            presentation=target, required_solo_competition=prerequisite,
        )
        cart, _ = Cart.objects.get_or_create(user=self.user)
        CartItem.objects.create(cart=cart, content_object=target)
        self.assertEqual(self.client.post('/api/orders/checkout/').status_code, 400)

        registration = SoloCompetitionRegistration.objects.create(
            user=self.user, solo_competition=prerequisite,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
        )
        order = Order.objects.create(user=self.user, subtotal_amount=100, total_amount=100)
        OrderItem.objects.create(order=order, content_object=target, price=100, description='Workshop')
        validate_order_items_for_payment(order)
        registration.status = SoloCompetitionRegistration.STATUS_CANCELLED
        registration.save(update_fields=['status'])
        with self.assertRaises(OrderPaymentEligibilityError):
            validate_order_items_for_payment(order)

    def test_pack_cannot_bypass_prerequisite(self):
        target = self.presentation(is_paid=True, price=Decimal('100'))
        prerequisite = self.solo()
        RegistrationPrerequisite.objects.create(
            presentation=target, required_solo_competition=prerequisite,
        )
        pack = Pack.objects.create(name='Pack', real_price=100)
        PackItem.objects.create(pack=pack, content_object=target)
        response = self.client.post('/api/cart/items/', {'item_type': 'pack', 'item_id': pack.pk})
        self.assertEqual(response.status_code, 400)
