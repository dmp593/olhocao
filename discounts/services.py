from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from discounts.models import (
    DiscountType,
    Promotion,
    PromotionRedemption,
    RedemptionStatus,
    SaleChannel,
    normalize_breed_name,
    normalize_promotion_code,
)

MONEY_PLACES = Decimal("0.01")
ACTIVE_REDEMPTION_STATUSES = (RedemptionStatus.RESERVED, RedemptionStatus.CONSUMED)


class PromotionUnavailable(ValueError):
    """A safe explanation for a promotion that cannot be used."""


@dataclass(frozen=True)
class DiscountLine:
    """One independently discountable gross-price line.

    ``service_end`` is exclusive when the line represents a date range. Catalog
    identifiers refer to the dynamic business hierarchy; ``host_catalog_type``
    identifies the parent service when this line is purchased as an add-on.
    """

    key: str
    amount_eur: Decimal
    sale_channel: str
    catalog_type_id: int | None
    catalog_level_id: int | None
    catalog_offer_id: int | None
    host_catalog_type_id: int | None = None
    pet_id: int | None = None
    breed: str = ""
    service_start: date | None = None
    service_end: date | None = None

    def __post_init__(self):
        amount = money(self.amount_eur)
        if amount <= Decimal("0.00"):
            raise ValueError("Discount lines must have a positive amount.")
        if self.sale_channel not in SaleChannel.values:
            raise ValueError("Discount line sale channel is invalid.")
        object.__setattr__(self, "amount_eur", amount)


@dataclass(frozen=True)
class PromotionQuote:
    promotion: Promotion
    subtotal_amount_eur: Decimal
    eligible_amount_eur: Decimal
    discount_amount_eur: Decimal
    total_amount_eur: Decimal
    allocations: dict[str, Decimal]


def quote_promotion(
    code,
    *,
    customer,
    lines,
    at=None,
    purchase_reference="",
    lock=False,
):
    """Validate a code and calculate an exact, deterministic discount quote."""

    normalized_code = normalize_promotion_code(code)
    if not normalized_code:
        raise PromotionUnavailable(_("Enter a promotion code."))

    queryset = Promotion.objects
    if lock:
        queryset = queryset.select_for_update()
    try:
        promotion = queryset.get(code__iexact=normalized_code)
    except Promotion.DoesNotExist as exc:
        raise PromotionUnavailable(_("This promotion code is not valid.")) from exc

    now = at or timezone.now()
    _validate_promotion_availability(
        promotion,
        customer=customer,
        now=now,
        purchase_reference=purchase_reference,
    )

    normalized_lines = tuple(lines)
    if not normalized_lines:
        raise PromotionUnavailable(_("There are no items to discount."))
    keys = [line.key for line in normalized_lines]
    if len(keys) != len(set(keys)):
        raise ValueError("Discount line keys must be unique.")

    catalog_type_ids = set(promotion.catalog_types.values_list("pk", flat=True))
    catalog_level_ids = set(promotion.catalog_levels.values_list("pk", flat=True))
    catalog_offer_ids = set(promotion.catalog_offers.values_list("pk", flat=True))
    host_catalog_type_ids = set(promotion.host_catalog_types.values_list("pk", flat=True))
    pet_ids = set(promotion.pets.values_list("pk", flat=True))
    breed_names = set(promotion.eligible_breeds or [])
    eligible_lines = tuple(
        line
        for line in normalized_lines
        if _line_is_eligible(
            line,
            promotion=promotion,
            catalog_type_ids=catalog_type_ids,
            catalog_level_ids=catalog_level_ids,
            catalog_offer_ids=catalog_offer_ids,
            host_catalog_type_ids=host_catalog_type_ids,
            pet_ids=pet_ids,
            breed_names=breed_names,
        )
    )
    if not eligible_lines:
        raise PromotionUnavailable(_("This promotion does not apply to the selected items."))

    subtotal = money(sum((line.amount_eur for line in normalized_lines), Decimal("0.00")))
    eligible_amount = money(sum((line.amount_eur for line in eligible_lines), Decimal("0.00")))
    minimum = promotion.minimum_eligible_amount_eur
    if minimum is not None and eligible_amount < minimum:
        raise PromotionUnavailable(
            _("This promotion requires at least %(amount).2f € of eligible items.")
            % {"amount": minimum}
        )

    discount_amount = _discount_amount(promotion, eligible_amount)
    allocations = allocate_discount(eligible_lines, discount_amount)
    return PromotionQuote(
        promotion=promotion,
        subtotal_amount_eur=subtotal,
        eligible_amount_eur=eligible_amount,
        discount_amount_eur=discount_amount,
        total_amount_eur=money(subtotal - discount_amount),
        allocations=allocations,
    )


@transaction.atomic
def reserve_promotion(
    code,
    *,
    customer,
    lines,
    purchase_kind,
    purchase_reference,
):
    """Reserve one promotion use while serializing limit checks."""

    existing = (
        PromotionRedemption.objects.select_for_update()
        .filter(
            purchase_reference=purchase_reference,
            status__in=ACTIVE_REDEMPTION_STATUSES,
        )
        .select_related("promotion")
        .first()
    )
    if existing:
        if existing.code_snapshot != normalize_promotion_code(code):
            raise PromotionUnavailable(
                _("A different promotion is already attached to this purchase.")
            )
        return quote_promotion(
            code,
            customer=customer,
            lines=lines,
            purchase_reference=purchase_reference,
            lock=True,
        ), existing

    quote = quote_promotion(
        code,
        customer=customer,
        lines=lines,
        purchase_reference=purchase_reference,
        lock=True,
    )
    redemption = PromotionRedemption.objects.create(
        promotion=quote.promotion,
        customer=customer,
        purchase_kind=purchase_kind,
        purchase_reference=purchase_reference,
        active_purchase_reference=purchase_reference,
        status=RedemptionStatus.RESERVED,
        subtotal_amount_eur=quote.subtotal_amount_eur,
        eligible_amount_eur=quote.eligible_amount_eur,
        discount_amount_eur=quote.discount_amount_eur,
        code_snapshot=quote.promotion.code,
        name_snapshot=quote.promotion.name,
        discount_type_snapshot=quote.promotion.discount_type,
        value_snapshot=quote.promotion.value,
    )
    return quote, redemption


@transaction.atomic
def consume_redemption(purchase_reference):
    redemption = (
        PromotionRedemption.objects.select_for_update()
        .filter(
            purchase_reference=purchase_reference,
            status__in=ACTIVE_REDEMPTION_STATUSES,
        )
        .first()
    )
    if redemption is None or redemption.status == RedemptionStatus.CONSUMED:
        return redemption
    redemption.status = RedemptionStatus.CONSUMED
    redemption.consumed_at = timezone.now()
    redemption.save(update_fields=("status", "consumed_at", "updated_at"))
    return redemption


@transaction.atomic
def release_redemption(purchase_reference):
    redemption = (
        PromotionRedemption.objects.select_for_update()
        .filter(
            purchase_reference=purchase_reference,
            status=RedemptionStatus.RESERVED,
        )
        .first()
    )
    if redemption is None:
        return None
    redemption.status = RedemptionStatus.RELEASED
    redemption.active_purchase_reference = None
    redemption.released_at = timezone.now()
    redemption.save(
        update_fields=(
            "status",
            "active_purchase_reference",
            "released_at",
            "updated_at",
        )
    )
    return redemption


def allocate_discount(lines, discount_amount):
    """Allocate an exact cent amount proportionally using largest remainders."""

    lines = tuple(lines)
    discount_amount = money(discount_amount)
    total = money(sum((line.amount_eur for line in lines), Decimal("0.00")))
    if discount_amount < Decimal("0.00") or discount_amount > total:
        raise ValueError("Discount allocation is outside the line total.")
    if discount_amount == Decimal("0.00"):
        return {line.key: Decimal("0.00") for line in lines}

    exact = [discount_amount * line.amount_eur / total for line in lines]
    rounded_down = [value.quantize(MONEY_PLACES, rounding=ROUND_DOWN) for value in exact]
    cents_left = int((discount_amount - sum(rounded_down, Decimal("0.00"))) / MONEY_PLACES)
    ranked_indexes = sorted(
        range(len(lines)),
        key=lambda index: (exact[index] - rounded_down[index], -index),
        reverse=True,
    )
    for index in ranked_indexes[:cents_left]:
        rounded_down[index] += MONEY_PLACES
    return {line.key: rounded_down[index] for index, line in enumerate(lines)}


def money(value):
    return Decimal(str(value)).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _validate_promotion_availability(
    promotion,
    *,
    customer,
    now,
    purchase_reference,
):
    if not promotion.is_active:
        raise PromotionUnavailable(_("This promotion is no longer active."))
    if promotion.starts_at and now < promotion.starts_at:
        raise PromotionUnavailable(_("This promotion is not available yet."))
    if promotion.ends_at and now >= promotion.ends_at:
        raise PromotionUnavailable(_("This promotion has expired."))
    if promotion.customers.exists() and not promotion.customers.filter(pk=customer.pk).exists():
        raise PromotionUnavailable(_("This promotion is not available for this customer."))

    active_uses = promotion.redemptions.filter(status__in=ACTIVE_REDEMPTION_STATUSES)
    if purchase_reference:
        active_uses = active_uses.exclude(purchase_reference=purchase_reference)
    if promotion.max_redemptions is not None and active_uses.count() >= promotion.max_redemptions:
        raise PromotionUnavailable(_("This promotion has reached its usage limit."))
    if (
        promotion.max_redemptions_per_customer is not None
        and active_uses.filter(customer=customer).count() >= promotion.max_redemptions_per_customer
    ):
        raise PromotionUnavailable(_("This customer has already used this promotion."))


def _line_is_eligible(
    line,
    *,
    promotion,
    catalog_type_ids,
    catalog_level_ids,
    catalog_offer_ids,
    host_catalog_type_ids,
    pet_ids,
    breed_names,
):
    if line.sale_channel not in promotion.eligible_sale_channels:
        return False
    if catalog_type_ids and line.catalog_type_id not in catalog_type_ids:
        return False
    if catalog_level_ids and line.catalog_level_id not in catalog_level_ids:
        return False
    if catalog_offer_ids and line.catalog_offer_id not in catalog_offer_ids:
        return False
    if host_catalog_type_ids and line.host_catalog_type_id not in host_catalog_type_ids:
        return False
    if pet_ids and line.pet_id not in pet_ids:
        return False
    if breed_names and normalize_breed_name(line.breed) not in breed_names:
        return False
    if not _service_dates_are_eligible(line, promotion):
        return False
    return True


def _service_dates_are_eligible(line, promotion):
    if not promotion.service_starts_on and not promotion.service_ends_on:
        return True
    if line.service_start is None or line.service_end is None:
        return False
    last_service_date = line.service_end - timedelta(days=1)
    if promotion.service_starts_on and line.service_start < promotion.service_starts_on:
        return False
    if promotion.service_ends_on and last_service_date > promotion.service_ends_on:
        return False
    return True


def _discount_amount(promotion, eligible_amount):
    if promotion.discount_type == DiscountType.FIXED:
        return min(money(promotion.value), eligible_amount)
    return min(
        money(eligible_amount * promotion.value / Decimal("100")),
        eligible_amount,
    )
