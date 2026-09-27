from decimal import Decimal

from django.apps import apps


def get_item_price(item_object):
    """Return the current cart price for any supported buyable object."""
    if item_object is None:
        return Decimal('0')

    Presentation = apps.get_model('events', 'Presentation')
    SoloCompetition = apps.get_model('events', 'SoloCompetition')
    CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
    Registration = apps.get_model('events', 'CompetitionTeamRegistration')
    Product = apps.get_model('shop', 'Product')
    Pack = apps.get_model('shop', 'Pack')

    if hasattr(item_object, 'is_paid') and not item_object.is_paid:
        return Decimal('0')
    if isinstance(item_object, Presentation):
        return item_object.price or Decimal('0')
    if isinstance(item_object, SoloCompetition):
        return item_object.price_per_participant or Decimal('0')
    if isinstance(item_object, CompetitionTeam):
        competition = item_object.group_competition
        if competition and competition.requires_payment():
            return competition.price_per_member * item_object.memberships.filter(status='accepted').count()
        return Decimal('0')
    if isinstance(item_object, Registration):
        return item_object.price
    if isinstance(item_object, Product):
        return item_object.price
    if isinstance(item_object, Pack):
        return item_object.real_price
    return Decimal('0')
