"""Application-service entry points called by payment-core settlement coordination."""


def settle_wallet_top_up(request):
    from .services import WalletService

    return WalletService.settle_verified_payment_topup(request)
