from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Min, Q
from django.db.models.functions import Lower
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from accounts.models import CustomerProfile
from discounts.forms import PromotionFilterForm, PromotionForm
from discounts.models import (
    Promotion,
    PromotionChangeEvent,
    PromotionChangeType,
    RedemptionStatus,
)
from olhocao.mixins import StaffRequiredMixin
from pets.models import Pet

AUDITED_FIELDS = (
    "code",
    "name",
    "discount_type",
    "value",
    "eligible_sale_channels",
    "eligible_breeds",
    "starts_at",
    "ends_at",
    "service_starts_on",
    "service_ends_on",
    "minimum_eligible_amount_eur",
    "max_redemptions",
    "max_redemptions_per_customer",
    "is_active",
)

AUDIENCE_PAGE_SIZE = 20


class PromotionAudienceSearchView(StaffRequiredMixin, View):
    """Return one bounded page for the promotion audience picker."""

    search_kinds = {"customers", "pets", "breeds"}

    def get(self, request, kind):
        if kind not in self.search_kinds:
            raise Http404
        query = request.GET.get("q", "").strip()[:100]
        page_number = request.GET.get("page", 1)
        paginator = Paginator(self._items(kind, query), AUDIENCE_PAGE_SIZE)
        page = paginator.get_page(page_number)
        return JsonResponse(
            {
                "results": [self._serialize(kind, item) for item in page.object_list],
                "next_page": page.next_page_number() if page.has_next() else None,
            }
        )

    @staticmethod
    def _items(kind, query):
        if kind == "customers":
            customers = CustomerProfile.objects.select_related("user").filter(
                user__is_staff=False,
                user__is_active=True,
            )
            if query:
                customers = customers.filter(
                    Q(user__first_name__icontains=query)
                    | Q(user__last_name__icontains=query)
                    | Q(user__username__icontains=query)
                    | Q(user__email__icontains=query)
                    | Q(billing_phone__icontains=query)
                    | Q(tax_number__icontains=query)
                )
            return customers.order_by("user__first_name", "user__last_name", "user__email", "pk")

        pets = Pet.objects.select_related("owner__user").filter(
            deleted_at__isnull=True,
            owner__user__is_active=True,
        )
        if kind == "pets":
            if query:
                pets = pets.filter(
                    Q(name__icontains=query)
                    | Q(breed__icontains=query)
                    | Q(owner__user__first_name__icontains=query)
                    | Q(owner__user__last_name__icontains=query)
                    | Q(owner__user__email__icontains=query)
                )
            return pets.order_by("name", "owner__user__email", "pk")

        if query:
            pets = pets.filter(breed__icontains=query)
        return (
            pets.exclude(breed="")
            .annotate(normalized_breed=Lower("breed"))
            .values("normalized_breed")
            .annotate(label=Min("breed"))
            .order_by("normalized_breed")
        )

    @staticmethod
    def _serialize(kind, item):
        if kind == "customers":
            return {
                "id": item.pk,
                "label": item.user.get_full_name() or item.user.username,
                "description": item.user.email,
            }
        if kind == "pets":
            return {
                "id": item.pk,
                "label": item.name,
                "description": _("%(breed)s · %(owner)s")
                % {"breed": item.breed, "owner": str(item.owner)},
            }
        return {
            "id": item["label"],
            "label": item["label"],
            "description": "",
        }


class PromotionListView(StaffRequiredMixin, ListView):
    template_name = "discounts/promotion_list.html"
    context_object_name = "promotions"
    paginate_by = 30

    def get_queryset(self):
        self.filter_form = PromotionFilterForm(self.request.GET)
        queryset = Promotion.objects.annotate(
            active_use_count=Count(
                "redemptions",
                filter=Q(
                    redemptions__status__in=(
                        RedemptionStatus.RESERVED,
                        RedemptionStatus.CONSUMED,
                    )
                ),
                distinct=True,
            ),
            consumed_use_count=Count(
                "redemptions",
                filter=Q(redemptions__status=RedemptionStatus.CONSUMED),
                distinct=True,
            ),
        )
        return self.filter_form.filter(queryset).order_by("-is_active", "code", "id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class PromotionCreateView(StaffRequiredMixin, CreateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "discounts/promotion_form.html"

    @transaction.atomic
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        PromotionChangeEvent.objects.create(
            promotion=self.object,
            change_type=PromotionChangeType.CREATED,
            actor=self.request.user,
            details={"current": _promotion_snapshot(self.object)},
        )
        messages.success(self.request, _("Promotion created."))
        return response

    def get_success_url(self):
        return reverse("discounts:promotion_update", kwargs={"pk": self.object.pk})


class PromotionUpdateView(StaffRequiredMixin, UpdateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "discounts/promotion_form.html"

    @transaction.atomic
    def form_valid(self, form):
        locked_promotion = Promotion.objects.select_for_update().get(pk=self.object.pk)
        before = _promotion_snapshot(locked_promotion)
        response = super().form_valid(form)
        after = _promotion_snapshot(self.object)
        changes = {
            field: {"from": before.get(field), "to": after.get(field)}
            for field in after
            if before.get(field) != after.get(field)
        }
        if changes:
            PromotionChangeEvent.objects.create(
                promotion=self.object,
                change_type=PromotionChangeType.UPDATED,
                actor=self.request.user,
                details={"changes": changes},
            )
        messages.success(self.request, _("Promotion updated."))
        return response

    def get_success_url(self):
        return reverse("discounts:promotion_update", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["redemptions"] = self.object.redemptions.select_related("customer__user")[:50]
        context["change_events"] = self.object.change_events.select_related("actor")[:30]
        return context


class PromotionToggleView(StaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk):
        promotion = Promotion.objects.select_for_update().get(pk=pk)
        promotion.is_active = not promotion.is_active
        promotion.save(update_fields=("is_active", "updated_at"))
        PromotionChangeEvent.objects.create(
            promotion=promotion,
            change_type=(
                PromotionChangeType.ACTIVATED
                if promotion.is_active
                else PromotionChangeType.DEACTIVATED
            ),
            actor=request.user,
            details={"is_active": promotion.is_active},
        )
        messages.success(
            request,
            _("Promotion activated.") if promotion.is_active else _("Promotion deactivated."),
        )
        return redirect("discounts:promotion_list")


def _promotion_snapshot(promotion):
    snapshot = {field: _serializable(getattr(promotion, field)) for field in AUDITED_FIELDS}
    snapshot.update(
        catalog_types=list(promotion.catalog_types.values_list("pk", flat=True)),
        catalog_levels=list(promotion.catalog_levels.values_list("pk", flat=True)),
        catalog_offers=list(promotion.catalog_offers.values_list("pk", flat=True)),
        host_catalog_types=list(promotion.host_catalog_types.values_list("pk", flat=True)),
        customers=list(promotion.customers.values_list("pk", flat=True)),
        pets=list(promotion.pets.values_list("pk", flat=True)),
    )
    return snapshot


def _serializable(value):
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)
