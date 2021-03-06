from decimal import Decimal
import textwrap

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from lib.brains.client import get_client
from lib.sellers.models import Seller, SellerProduct
from payments_config import sellers
from solitude.logger import getLogger

log = getLogger('s.brains.management')


class BraintreePlanDoesNotExist(Exception):
    """
    Failed to look up a recurring payment plan in the Braintree API.
    """


def get_or_create_seller(uuid):
    """
    Create a seller in solitude for the product.
    """
    # We'll suffix with the BRAINTREE_MERCHANT_ID so that the seller will
    # change if you change ids, or servers.
    uuid = uuid + '-' + settings.BRAINTREE_MERCHANT_ID
    seller, created = Seller.objects.get_or_create(uuid=uuid)
    log.info('Seller {0}, pk: {1}'
             .format('created' if created else 'exists', seller.pk))
    return seller


def get_or_create_seller_product(external_id, public_id, seller):
    """
    Create a seller product in solitude for each product.
    """
    seller_product, created = SellerProduct.objects.get_or_create(
        external_id=external_id, public_id=public_id, seller=seller)
    log.info('SellerProduct {0}, uuid: {1}'
             .format('created' if created else 'exists', seller_product.pk))
    return seller_product


def product_exists(plans, external_id, amount):
    """
    Check that the product exists in Braintree.
    """
    if external_id not in plans:
        raise BraintreePlanDoesNotExist(
            'plan does not exist: {}'.format(external_id))

    plan = plans[external_id]
    if amount:
        # This only applies to products with a configured amount.
        # For example: donations do not have a configured amount.
        # To be more specific: the braintree dashboard won't let you create
        # a plan with an empty price so you have to use $0.00
        if Decimal(plan.price) != amount:
            log.warning(
                'The plan: {0} in Braintree has a different amount ({1}) '
                'from the configuration file ({2}). It will need to be '
                'updated in Braintree.'
                .format(external_id, plan.price, amount))

            raise CommandError('Different price: {0}'.format(external_id))

    if plan.billing_day_of_month:
        log.warning(
            'The plan: {} in Braintree has a billing_day_of_month set, which '
            'is not supported at the moment.'.format(external_id)
        )
        raise CommandError('Unsupported billing_day_of_month: {}'
                           .format(external_id))

    if plan.trial_period:
        log.warning(
            'The plan: {} in Braintree has a trial_period set, which is not '
            'supported at the moment.'.format(external_id)
        )
        raise CommandError('Unsupported trial_period: {}'.format(external_id))

    log.info('Plan: {0} exists correctly on Braintree'.format(external_id))


def get_plans(client):
    return dict((p.id, p) for p in client.Plan.all())


class Command(BaseCommand):
    help = 'Creates products in solitude and braintree from configuration.'

    def handle(self, *args, **options):
        client = get_client()
        for seller_name, seller_config in sellers.items():
            log.info('Configuring: {0}'.format(seller_name))
            seller = get_or_create_seller(seller_name)
            plans = get_plans(client)
            # Iterate through each product checking they exist.
            for product in seller_config.products:
                # Check that the product exists in Braintree.
                get_or_create_seller_product(
                    external_id=product.id,
                    public_id=product.id,
                    seller=seller
                )
                if product.recurrence:
                    # If there's recurrence, we need to check
                    # that it exists in Braintree and is set up ok.
                    try:
                        product_exists(plans, product.id, product.amount)
                    except BraintreePlanDoesNotExist:
                        raise CommandError(textwrap.dedent('''\
                            Missing product: {product.id}

                            Currently it's not possible to automate this.

                            1. Log into https://sandbox.braintreegateway.com/
                               (or the production dashboard)
                            2. Go to Recurring Billing > Plans from the sidebar
                            3. Enter the following data:

                                Plan ID: {product.id}
                                Plan Name: {product.description}
                                Price: {price}
                                Billing Cycle Every: 1 month

                            4. re-run this script
                            5. MFBT
                        '''.format(
                            # TODO: show the right thing for recurrence
                            # other than 'month'
                            product=product,
                            price=(product.amount or '0.00'),
                        )))
