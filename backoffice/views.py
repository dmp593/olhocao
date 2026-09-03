import json
import logging
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)
from django.views.generic.edit import FormView

from accounts.forms import UserChangeForm
from accounts.models import CustomerProfile, CustomerSyncStatus
from accounts.services.invitations import send_customer_invitation
from activities.models import (
    CONSUMED_WEEKLY_USAGE_STATUSES,
    RESERVED_PLACE_STATUSES,
    ActivityReservation,
    ActivitySession,
    ActivitySessionEvent,
    EntitlementStatus,
    OfferingMode,
    OperationalClosure,
    RefundStatus,
    ReservationStatus,
    ServiceEntitlement,
    ServiceFulfilment,
    ServiceFulfilmentStatus,
    SessionSeries,
    SubscriptionCycle,
    SubscriptionCycleStatus,
    Trainer,
    WeeklyUsageAdjustment,
)
from activities.services.calendar import materialize_session_series
from activities.services.fulfilments import (
    ServiceFulfilmentError,
    cancel_fulfilment,
    complete_fulfilment,
    schedule_fulfilment,
)
from activities.services.refunds import request_entitlement_refund
from activities.services.reservations import (
    ActivityReservationError,
    cancel_activity_session,
    cancel_reservation,
    notify_activity_session_rescheduled,
    set_reservation_attendance,
)
from activities.services.subscriptions import (
    cancel_subscription_at_period_end,
    request_subscription_cycle_refund,
    retry_subscription_documents,
    schedule_subscription_resume,
    set_entitlement_access_block,
    set_subscription_access_block,
    suspend_subscription,
    synchronize_subscription,
    terminate_subscription,
)
from catalog.models import AddOnAvailability, CatalogLevel, CatalogOffer, CatalogType
from catalog.services.add_ons import (
    InvalidOptionalServiceConfiguration,
    optional_service_offers,
    replace_optional_services,
)
from catalog.services.ordering import InvalidCatalogLevelOrder, reorder_catalog_levels
from frontoffice.models import ContactRequest, SiteSettings
from hotel import models as hotel_models
from hotel.models import BookingStay, CatalogItem, CatalogSyncStatus
from hotel.services.catalog import (
    CatalogSyncError,
    TocOnlineCatalogSynchronizer,
    catalog_version_history,
)
from hotel.services.payments import PaymentGatewayError
from olhocao.mixins import LoginRequiredMixin, StaffRequiredMixin, UserPassesTestMixin
from pets.models import Pet

from .calendar_forms import (
    OperationalClosureForm,
    SessionSeriesForm,
    TrainerForm,
    WeeklyUsageAdjustmentForm,
)
from .catalog_forms import (
    CatalogLevelForm,
    CatalogOfferForm,
    CatalogTypeForm,
    OptionalServiceConfigurationForm,
    resolve_optional_service_target,
)
from .forms import (
    ActivityReservationCancellationForm,
    ActivityReservationFilterForm,
    ActivitySessionCancellationForm,
    ActivitySessionForm,
    BookingStayFilterForm,
    CustomerInvitationForm,
    CustomerListFilterForm,
    EntitlementAccessForm,
    EntitlementRefundForm,
    LegalDocumentForm,
    ServiceFulfilmentManagementForm,
    SiteSettingsForm,
    SubscriptionFilterForm,
    SubscriptionManagementForm,
    SubscriptionRefundForm,
    create_lineitem_formset,
    create_section_formset,
)
from .models import LegalDocument, LegalDocumentSection
from .services.bookings import filtered_booking_stays, operational_stays
from .site_content import CONTENT_COLLECTIONS, SITE_SETTINGS_FIELDSETS, get_content_collection

logger = logging.getLogger(__name__)


class SiteContentIndexView(StaffRequiredMixin, TemplateView):
    template_name = "backoffice/site_content_index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["content_collections"] = [
            {
                "definition": collection,
                "published_count": collection.model.objects.filter(is_active=True).count(),
                "total_count": collection.model.objects.count(),
            }
            for collection in CONTENT_COLLECTIONS
        ]
        return context


class SiteSettingsUpdateView(StaffRequiredMixin, UpdateView):
    model = SiteSettings
    form_class = SiteSettingsForm
    template_name = "backoffice/site_settings_form.html"

    def get_object(self, queryset=None):
        settings = SiteSettings.current()
        if settings is None:
            raise Http404("Website settings have not been initialized.")
        return settings

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, _("Website settings updated."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        context["settings_fieldsets"] = [
            {
                "title": title,
                "description": description,
                "fields": [form[field_name] for field_name in field_names],
            }
            for title, description, field_names in SITE_SETTINGS_FIELDSETS
        ]
        return context

    def get_success_url(self):
        return reverse("backoffice:site_content")


class ContentCollectionMixin(StaffRequiredMixin):
    collection = None

    def dispatch(self, request, *args, **kwargs):
        self.collection = get_content_collection(kwargs["collection"])
        if self.collection is None:
            raise Http404("Unknown website content collection.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        return self.collection.form_class

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["collection"] = self.collection
        return context


class ContentCollectionListView(ContentCollectionMixin, TemplateView):
    template_name = "backoffice/site_content_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["content_entries"] = self.collection.model.objects.order_by("order", "id")
        return context


class ContentEntryCreateView(ContentCollectionMixin, CreateView):
    template_name = "backoffice/site_content_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(
            self.request,
            _("The %(content_type)s was created.") % {"content_type": self.collection.singular},
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "backoffice:site_content_collection",
            kwargs={"collection": self.collection.slug},
        )


class ContentEntryUpdateView(ContentCollectionMixin, UpdateView):
    template_name = "backoffice/site_content_form.html"

    def get_queryset(self):
        return self.collection.model.objects.all()

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(
            self.request,
            _("The %(content_type)s was updated.") % {"content_type": self.collection.singular},
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "backoffice:site_content_collection",
            kwargs={"collection": self.collection.slug},
        )


class ActivitiesDashboardView(StaffRequiredMixin, TemplateView):
    template_name = "backoffice/activities_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        form = ActivityReservationFilterForm(self.request.GET or None)
        filters = form.cleaned_data if form.is_valid() else {}
        reservations = ActivityReservation.objects.select_related(
            "session",
            "session__catalog_type",
            "pet",
            "entitlement__customer__user",
            "entitlement__offering__catalog_offer__catalog_item",
        ).order_by("session__starts_at", "pet__name")
        query = filters.get("q")
        if query:
            reservations = reservations.filter(
                Q(pet__name__icontains=query)
                | Q(entitlement__customer__user__first_name__icontains=query)
                | Q(entitlement__customer__user__last_name__icontains=query)
                | Q(entitlement__customer__user__email__icontains=query)
            )
        if filters.get("start_date"):
            reservations = reservations.filter(session__starts_at__date__gte=filters["start_date"])
        if filters.get("end_date"):
            reservations = reservations.filter(session__starts_at__date__lte=filters["end_date"])
        if filters.get("catalog_type"):
            reservations = reservations.filter(session__catalog_type=filters["catalog_type"])
        if filters.get("status"):
            reservations = reservations.filter(status=filters["status"])
        if filters.get("medical_priority"):
            reservations = reservations.filter(~Q(pet__medication="") | ~Q(pet__allergies=""))

        fulfilments = (
            ServiceFulfilment.objects.exclude(
                status__in=(
                    ServiceFulfilmentStatus.AWAITING_PAYMENT,
                    ServiceFulfilmentStatus.COMPLETED,
                    ServiceFulfilmentStatus.CANCELLED,
                )
            )
            .select_related(
                "pet",
                "entitlement__customer__user",
                "entitlement__offering__catalog_offer__catalog_type",
                "booking_service__stay__booking__customer__user",
                "booking_service__catalog_offer__catalog_type",
            )
            .order_by("scheduled_starts_at", "created_at")
        )
        if query:
            fulfilments = fulfilments.filter(
                Q(pet__name__icontains=query)
                | Q(entitlement__customer__user__first_name__icontains=query)
                | Q(entitlement__customer__user__last_name__icontains=query)
                | Q(entitlement__customer__user__email__icontains=query)
                | Q(booking_service__stay__booking__customer__user__first_name__icontains=query)
                | Q(booking_service__stay__booking__customer__user__last_name__icontains=query)
                | Q(booking_service__stay__booking__customer__user__email__icontains=query)
            )
        if filters.get("catalog_type"):
            catalog_type = filters["catalog_type"]
            fulfilments = fulfilments.filter(
                Q(entitlement__catalog_type_reference=catalog_type.pk)
                | Q(booking_service__catalog_type_reference=catalog_type.pk)
            )
        if filters.get("medical_priority"):
            fulfilments = fulfilments.filter(~Q(pet__medication="") | ~Q(pet__allergies=""))

        paginator = Paginator(reservations, 30)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        today_sessions = ActivitySession.objects.filter(
            starts_at__date=today,
            is_cancelled=False,
        )
        today_reservations = ActivityReservation.objects.filter(
            session__starts_at__date=today,
            status__in=RESERVED_PLACE_STATUSES,
        )
        context.update(
            {
                "filter_form": form,
                "reservations": page_obj.object_list,
                "page_obj": page_obj,
                "paginator": paginator,
                "is_paginated": page_obj.has_other_pages(),
                "stats": {
                    "sessions_today": today_sessions.count(),
                    "dogs_today": today_reservations.count(),
                    "medical_today": today_reservations.filter(pet__medication__gt="").count(),
                    "payment_attention": ServiceEntitlement.objects.filter(
                        Q(status=EntitlementStatus.PAST_DUE)
                        | Q(access_blocked_at__isnull=False)
                        | ~Q(last_payment_error="")
                    )
                    .distinct()
                    .count(),
                },
                "upcoming_sessions": ActivitySession.objects.filter(
                    starts_at__gte=timezone.now(),
                    is_cancelled=False,
                )
                .select_related("catalog_type")
                .prefetch_related("trainers", "eligible_levels")
                .annotate(
                    booked_places=Count(
                        "reservations",
                        filter=Q(reservations__status__in=RESERVED_PLACE_STATUSES),
                    )
                )
                .order_by("starts_at")[:12],
                "open_fulfilments": fulfilments.distinct()[:20],
            }
        )
        return context


class ServiceFulfilmentDetailView(StaffRequiredMixin, FormView):
    template_name = "backoffice/service_fulfilment_detail.html"
    form_class = ServiceFulfilmentManagementForm

    def dispatch(self, request, *args, **kwargs):
        self.fulfilment = get_object_or_404(
            ServiceFulfilment.objects.select_related(
                "pet",
                "entitlement__customer__user",
                "entitlement__offering__catalog_offer__catalog_type",
                "booking_service__stay__booking__customer__user",
                "booking_service__catalog_offer__catalog_type",
            ),
            pk=kwargs["pk"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["fulfilment"] = self.fulfilment
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            fulfilment=self.fulfilment,
            events=self.fulfilment.events.select_related("actor"),
        )
        return context

    def form_valid(self, form):
        action = form.cleaned_data["action"]
        reason = form.cleaned_data["reason"]
        try:
            if action == form.SCHEDULE:
                schedule_fulfilment(
                    self.fulfilment,
                    starts_at=form.cleaned_data["starts_at"],
                    ends_at=form.cleaned_data.get("ends_at"),
                    actor=self.request.user,
                    reason=reason,
                    internal_notes=form.cleaned_data.get("internal_notes", ""),
                    override_closure=form.cleaned_data.get("override_policy", False),
                )
                success_message = _("The service appointment was saved.")
            elif action == form.COMPLETE:
                complete_fulfilment(
                    self.fulfilment,
                    actor=self.request.user,
                    reason=reason,
                    internal_notes=form.cleaned_data.get("internal_notes", ""),
                )
                success_message = _("The service was marked as completed.")
            elif action == form.CANCEL:
                cancel_fulfilment(
                    self.fulfilment,
                    actor=self.request.user,
                    reason=reason,
                    override_policy=form.cleaned_data.get("override_policy", False),
                )
                success_message = _("The service was cancelled.")
            else:
                raise ServiceFulfilmentError(_("Choose a valid management action."))
        except ServiceFulfilmentError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        messages.success(self.request, success_message)
        return redirect("backoffice:service_fulfilment_detail", pk=self.fulfilment.pk)


class SubscriptionDashboardView(StaffRequiredMixin, TemplateView):
    template_name = "backoffice/subscription_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        form = SubscriptionFilterForm(
            self.request.GET or None,
            initial={
                "start_date": today,
                "end_date": today + timedelta(days=30),
            },
        )
        filters = form.cleaned_data if form.is_valid() else {}
        start_date = filters.get("start_date") or today
        end_date = filters.get("end_date") or today + timedelta(days=30)

        subscriptions = ServiceEntitlement.objects.filter(
            offering_mode_snapshot=OfferingMode.SUBSCRIPTION,
        ).select_related(
            "customer__user",
            "pet",
            "offering__catalog_offer__catalog_item",
        )
        query = filters.get("q")
        if query:
            subscriptions = subscriptions.filter(
                Q(pet__name__icontains=query)
                | Q(customer__user__first_name__icontains=query)
                | Q(customer__user__last_name__icontains=query)
                | Q(customer__user__email__icontains=query)
            )
        if filters.get("catalog_type"):
            subscriptions = subscriptions.filter(catalog_type_reference=filters["catalog_type"].pk)
        if filters.get("catalog_level"):
            subscriptions = subscriptions.filter(
                catalog_level_reference=filters["catalog_level"].pk
            )
        if filters.get("status"):
            subscriptions = subscriptions.filter(status=filters["status"])
        if filters.get("medical_priority"):
            subscriptions = subscriptions.filter(~Q(pet__medication="") | ~Q(pet__allergies=""))

        week_start = timezone.now() - timedelta(days=timezone.localdate().weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)
        weekly_extra_uses = (
            WeeklyUsageAdjustment.objects.filter(
                entitlement_id=OuterRef("pk"),
                week_start=week_start.date(),
            )
            .values("entitlement_id")
            .annotate(total=Sum("extra_uses"))
            .values("total")
        )
        subscriptions = subscriptions.annotate(
            weekly_usage=Count(
                "reservations",
                filter=Q(
                    reservations__status__in=CONSUMED_WEEKLY_USAGE_STATUSES,
                    reservations__session__starts_at__gte=week_start,
                    reservations__session__starts_at__lt=week_end,
                ),
                distinct=True,
            ),
            weekly_extra_uses=Coalesce(
                Subquery(weekly_extra_uses, output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            ),
        ).order_by("catalog_type_name_snapshot", "pet__name", "customer__user__last_name")

        active_subscriptions = subscriptions.filter(
            status__in=(
                EntitlementStatus.ACTIVE,
                EntitlementStatus.PAST_DUE,
                EntitlementStatus.CANCEL_AT_PERIOD_END,
            )
        ).filter(Q(current_period_end__isnull=True) | Q(current_period_end__gt=timezone.now()))

        sessions = ActivitySession.objects.select_related("catalog_type").filter(
            starts_at__date__gte=start_date,
            starts_at__date__lte=end_date,
            is_cancelled=False,
        )
        if filters.get("catalog_type"):
            sessions = sessions.filter(catalog_type=filters["catalog_type"])
        if filters.get("catalog_level"):
            sessions = sessions.filter(eligible_levels=filters["catalog_level"])
        sessions = sessions.annotate(
            booked_places=Count(
                "reservations",
                filter=Q(reservations__status__in=RESERVED_PLACE_STATUSES),
            )
        ).order_by("starts_at")

        daily_capacity = {}
        total_capacity = 0
        total_booked = 0
        for session in sessions:
            key = (timezone.localtime(session.starts_at).date(), session.catalog_type_id)
            daily_capacity.setdefault(
                key,
                {
                    "activity_label": session.catalog_type.name,
                    "capacity": 0,
                    "booked": 0,
                },
            )
            daily_capacity[key]["capacity"] += session.capacity
            daily_capacity[key]["booked"] += session.booked_places
            total_capacity += session.capacity
            total_booked += session.booked_places
        capacity_rows = [
            {
                "date": date,
                "catalog_type_id": catalog_type_id,
                "activity_label": values["activity_label"],
                "capacity": values["capacity"],
                "booked": values["booked"],
                "available": max(values["capacity"] - values["booked"], 0),
                "utilization": round(values["booked"] / values["capacity"] * 100)
                if values["capacity"]
                else 0,
            }
            for (date, catalog_type_id), values in sorted(daily_capacity.items())
        ]

        contracted_visits = sum(
            entitlement.sessions_per_week_snapshot or 0 for entitlement in active_subscriptions
        )
        period_days = (end_date - start_date).days + 1
        period_weeks = max(period_days / 7, 1)
        demand_rows = []
        scheduled_types = CatalogType.objects.filter(
            is_active=True,
            workflow__in=("full_day_slot", "timed_slot"),
        ).order_by("order", "name")
        for catalog_type in scheduled_types:
            activity_subscriptions = active_subscriptions.filter(
                catalog_type_reference=catalog_type.pk
            )
            activity_sessions = [
                session for session in sessions if session.catalog_type_id == catalog_type.pk
            ]
            activity_capacity = sum(session.capacity for session in activity_sessions)
            activity_booked = sum(session.booked_places for session in activity_sessions)
            demand_rows.append(
                {
                    "activity_label": catalog_type.name,
                    "active_dogs": activity_subscriptions.count(),
                    "contracted_visits": sum(
                        entitlement.sessions_per_week_snapshot or 0
                        for entitlement in activity_subscriptions
                    ),
                    "weekly_capacity": round(activity_capacity / period_weeks, 1),
                    "weekly_booked": round(activity_booked / period_weeks, 1),
                }
            )
        paginator = Paginator(subscriptions, 30)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        for entitlement in page_obj.object_list:
            entitlement.weekly_allowance = (
                entitlement.sessions_per_week_snapshot or 0
            ) + entitlement.weekly_extra_uses
        context.update(
            {
                "filter_form": form,
                "start_date": start_date,
                "end_date": end_date,
                "subscriptions": page_obj.object_list,
                "page_obj": page_obj,
                "paginator": paginator,
                "is_paginated": page_obj.has_other_pages(),
                "capacity_rows": capacity_rows,
                "demand_rows": demand_rows,
                "stats": {
                    "active_subscriptions": active_subscriptions.count(),
                    "scheduled_types": scheduled_types.count(),
                    "contracted_visits": contracted_visits,
                    "available_places": max(total_capacity - total_booked, 0),
                    "utilization": round(total_booked / total_capacity * 100)
                    if total_capacity
                    else 0,
                    "payment_attention": subscriptions.filter(
                        Q(status=EntitlementStatus.PAST_DUE)
                        | Q(access_blocked_at__isnull=False)
                        | Q(subscription_cycles__status=SubscriptionCycleStatus.DISPUTED)
                        | ~Q(last_payment_error="")
                    )
                    .distinct()
                    .count(),
                },
            }
        )
        return context


class SubscriptionDetailView(StaffRequiredMixin, DetailView):
    template_name = "backoffice/subscription_detail.html"
    context_object_name = "entitlement"

    def get_queryset(self):
        return ServiceEntitlement.objects.filter(
            offering_mode_snapshot=OfferingMode.SUBSCRIPTION,
        ).select_related(
            "customer__user",
            "pet",
            "offering__catalog_offer__catalog_item",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "management_form": SubscriptionManagementForm(entitlement=self.object),
                "refund_form": SubscriptionRefundForm(),
                "cycles": self.object.subscription_cycles.all(),
                "management_events": self.object.management_events.select_related("actor"),
                "weekly_adjustments": self.object.weekly_usage_adjustments.select_related(
                    "created_by"
                ),
            }
        )
        return context


class SubscriptionActionView(StaffRequiredMixin, View):
    def post(self, request, pk):
        entitlement = get_object_or_404(
            ServiceEntitlement.objects.select_related("offering"),
            pk=pk,
            offering_mode_snapshot=OfferingMode.SUBSCRIPTION,
        )
        form = SubscriptionManagementForm(request.POST, entitlement=entitlement)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("backoffice:subscription_detail", pk=pk)

        action = form.cleaned_data["action"]
        reason = form.cleaned_data["reason"]
        try:
            if action == form.SUSPEND:
                suspend_subscription(entitlement, actor=request.user, reason=reason)
            elif action == form.RESUME:
                schedule_subscription_resume(
                    entitlement,
                    actor=request.user,
                    reason=reason,
                    resume_at=form.cleaned_data["resume_at"],
                )
            elif action == form.CANCEL_AT_PERIOD_END:
                cancel_subscription_at_period_end(
                    entitlement,
                    actor=request.user,
                    reason=reason,
                )
            elif action == form.TERMINATE:
                terminate_subscription(entitlement, actor=request.user, reason=reason)
            elif action == form.BLOCK_ACCESS:
                set_subscription_access_block(
                    entitlement,
                    actor=request.user,
                    reason=reason,
                    blocked=True,
                )
            elif action == form.RESTORE_ACCESS:
                set_subscription_access_block(
                    entitlement,
                    actor=request.user,
                    reason=reason,
                    blocked=False,
                )
            elif action == form.SYNCHRONIZE:
                synchronize_subscription(entitlement, actor=request.user, reason=reason)
            elif action == form.RETRY_DOCUMENTS:
                retry_subscription_documents(entitlement, actor=request.user, reason=reason)
        except Exception as exc:
            logger.exception(
                "Subscription administration action failed",
                extra={"entitlement_id": entitlement.pk, "action": action},
            )
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Subscription updated."))
        return redirect("backoffice:subscription_detail", pk=pk)


class SubscriptionCycleRefundView(StaffRequiredMixin, View):
    def post(self, request, pk):
        cycle = get_object_or_404(
            SubscriptionCycle.objects.select_related("entitlement"),
            pk=pk,
            status=SubscriptionCycleStatus.PAID,
        )
        form = SubscriptionRefundForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        else:
            try:
                request_subscription_cycle_refund(
                    cycle,
                    amount=form.cleaned_data["amount"],
                    actor=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except Exception as exc:
                logger.exception(
                    "Subscription cycle refund request failed",
                    extra={"cycle_id": cycle.pk},
                )
                messages.error(request, str(exc))
            else:
                messages.success(request, _("The refund was requested."))
        return redirect("backoffice:subscription_detail", pk=cycle.entitlement_id)


class EntitlementDetailView(StaffRequiredMixin, DetailView):
    """Administrative view of one immutable one-off or pack purchase."""

    template_name = "backoffice/entitlement_detail.html"
    context_object_name = "entitlement"

    def get_queryset(self):
        return ServiceEntitlement.objects.filter(
            offering_mode_snapshot__in=(OfferingMode.ONE_OFF, OfferingMode.PACK),
        ).select_related(
            "customer__user",
            "pet",
            "offering__catalog_offer__catalog_item",
            "offering__catalog_offer__catalog_type",
            "offering__catalog_offer__level",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        refunds = self.object.refunds.select_related("requested_by")
        reserved_refund_total = refunds.filter(
            status__in=(RefundStatus.PENDING, RefundStatus.REFUNDED),
        ).aggregate(total=Sum("amount_eur"))["total"] or Decimal("0.00")
        refundable_balance = max(
            self.object.total_price_eur - reserved_refund_total,
            Decimal("0.00"),
        )
        context.update(
            {
                "credit_entries": self.object.credit_entries.select_related("created_by"),
                "fulfilments": self.object.fulfilments.order_by("source_unit_number", "id"),
                "management_events": self.object.management_events.select_related("actor"),
                "refunds": refunds,
                "refundable_balance": refundable_balance,
                "can_request_refund": bool(
                    self.object.paid_at
                    and self.object.provider_capture_id
                    and refundable_balance > Decimal("0.00")
                ),
                "refund_form": EntitlementRefundForm(
                    initial={
                        "amount": refundable_balance,
                        "revoke_remaining_access": True,
                    }
                ),
                "access_form": EntitlementAccessForm(
                    initial={
                        "action": (
                            EntitlementAccessForm.RESTORE
                            if self.object.access_blocked_at
                            else EntitlementAccessForm.BLOCK
                        )
                    }
                ),
            }
        )
        return context


class EntitlementRefundView(StaffRequiredMixin, View):
    def post(self, request, pk):
        entitlement = get_object_or_404(
            ServiceEntitlement,
            pk=pk,
            offering_mode_snapshot__in=(OfferingMode.ONE_OFF, OfferingMode.PACK),
        )
        form = EntitlementRefundForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("backoffice:entitlement_detail", pk=entitlement.pk)

        try:
            request_entitlement_refund(
                entitlement,
                amount=form.cleaned_data["amount"],
                actor=request.user,
                reason=form.cleaned_data["reason"],
                revoke_remaining_access=form.cleaned_data["revoke_remaining_access"],
            )
        except PaymentGatewayError as exc:
            logger.exception(
                "Activity purchase refund request failed",
                extra={"entitlement_id": entitlement.pk},
            )
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _(
                    "The refund was requested. Access changes and the credit note "
                    "are applied only after provider confirmation."
                ),
            )
        return redirect("backoffice:entitlement_detail", pk=entitlement.pk)


class EntitlementAccessView(StaffRequiredMixin, View):
    def post(self, request, pk):
        entitlement = get_object_or_404(ServiceEntitlement, pk=pk)
        form = EntitlementAccessForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Choose an access action and provide a reason."))
            return redirect(self._return_url(request, entitlement))

        blocked = form.cleaned_data["action"] == EntitlementAccessForm.BLOCK
        set_entitlement_access_block(
            entitlement,
            actor=request.user,
            reason=form.cleaned_data["reason"],
            blocked=blocked,
        )
        messages.success(
            request,
            _("Operational access was blocked.")
            if blocked
            else _("Operational access was restored."),
        )
        return redirect(self._return_url(request, entitlement))

    @staticmethod
    def _return_url(request, entitlement):
        fallback = (
            reverse("backoffice:subscription_detail", kwargs={"pk": entitlement.pk})
            if entitlement.offering_mode_snapshot == OfferingMode.SUBSCRIPTION
            else reverse("backoffice:entitlement_detail", kwargs={"pk": entitlement.pk})
        )
        return _safe_return_url(request, request.POST.get("next"), fallback=fallback)


class ActivitySessionCreateView(StaffRequiredMixin, CreateView):
    model = ActivitySession
    form_class = ActivitySessionForm
    template_name = "backoffice/activity_session_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        _record_activity_session_change(
            form,
            actor=self.request.user,
            action="created",
        )
        messages.success(self.request, _("Session scheduled."))
        return response

    def get_success_url(self):
        return reverse("backoffice:activities_dashboard")


class ActivitySessionUpdateView(StaffRequiredMixin, UpdateView):
    model = ActivitySession
    form_class = ActivitySessionForm
    template_name = "backoffice/activity_session_form.html"

    def form_valid(self, form):
        previous_session = ActivitySession.objects.get(pk=form.instance.pk)
        with transaction.atomic():
            response = super().form_valid(form)
            _record_activity_session_change(
                form,
                actor=self.request.user,
                action="updated",
                previous_session=previous_session,
            )
            notify_activity_session_rescheduled(
                self.object,
                previous_starts_at=previous_session.starts_at,
                previous_ends_at=previous_session.ends_at,
            )
        messages.success(self.request, _("Session updated."))
        return response

    def get_success_url(self):
        return reverse("backoffice:calendar")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cancellation_form"] = ActivitySessionCancellationForm()
        context["session_events"] = self.object.events.select_related("actor")
        return context


class ActivitySessionCancelView(StaffRequiredMixin, View):
    def post(self, request, pk):
        session = get_object_or_404(ActivitySession, pk=pk)
        form = ActivitySessionCancellationForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("backoffice:activity_session_edit", pk=session.pk)
        try:
            _cancelled_session, reservation_count = cancel_activity_session(
                session,
                actor=request.user,
                reason=form.cleaned_data["reason"],
            )
        except ActivityReservationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(
                request,
                _("Session cancelled; %(count)s customer reservations were released.")
                % {"count": reservation_count},
            )
        return redirect("backoffice:calendar")


def _record_activity_session_change(form, *, actor, action, previous_session=None):
    session = form.instance
    previous_schedule = None
    if previous_session is not None:
        previous_schedule = {
            "starts_at": previous_session.starts_at.isoformat(),
            "ends_at": previous_session.ends_at.isoformat(),
            "capacity": previous_session.capacity,
            "catalog_type_id": previous_session.catalog_type_id,
        }
    ActivitySessionEvent.objects.create(
        session=session,
        action=action,
        actor=actor,
        details={
            "catalog_type_id": session.catalog_type_id,
            "starts_at": session.starts_at.isoformat(),
            "ends_at": session.ends_at.isoformat(),
            "capacity": session.capacity,
            "trainer_ids": list(session.trainers.values_list("pk", flat=True)),
            "eligible_level_ids": list(session.eligible_levels.values_list("pk", flat=True)),
            "operational_override": bool(form.cleaned_data.get("override_operational_rules")),
            "override_reason": str(form.cleaned_data.get("override_reason") or ""),
            "conflicts": [
                str(conflict) for conflict in form.cleaned_data.get("operational_conflicts", ())
            ],
            "previous_schedule": previous_schedule,
        },
    )


class CalendarView(StaffRequiredMixin, TemplateView):
    template_name = "backoffice/calendar.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start_date = self.request.GET.get("from") or timezone.localdate()
        if isinstance(start_date, str):
            try:
                start_date = timezone.datetime.fromisoformat(start_date).date()
            except ValueError:
                start_date = timezone.localdate()
        end_date = start_date + timedelta(days=41)
        sessions = (
            ActivitySession.objects.filter(
                starts_at__date__gte=start_date,
                starts_at__date__lte=end_date,
            )
            .select_related("catalog_type", "source_series")
            .prefetch_related("trainers", "eligible_levels")
            .annotate(
                booked_places=Count(
                    "reservations",
                    filter=Q(reservations__status__in=RESERVED_PLACE_STATUSES),
                )
            )
            .order_by("starts_at")
        )
        context.update(
            {
                "sessions": sessions,
                "start_date": start_date,
                "end_date": end_date,
                "previous_date": start_date - timedelta(days=42),
                "next_date": start_date + timedelta(days=42),
                "closures": OperationalClosure.objects.filter(
                    is_active=True,
                    starts_at__date__lte=end_date,
                    ends_at__date__gte=start_date,
                ).prefetch_related("catalog_types"),
            }
        )
        return context


class TrainerListView(StaffRequiredMixin, ListView):
    model = Trainer
    context_object_name = "trainers"
    template_name = "backoffice/trainer_list.html"


class TrainerFormMixin(StaffRequiredMixin):
    model = Trainer
    form_class = TrainerForm
    template_name = "backoffice/trainer_form.html"

    def form_valid(self, form):
        messages.success(
            self.request,
            _("Trainer updated.") if self.object else _("Trainer created."),
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("backoffice:trainers")


class TrainerCreateView(TrainerFormMixin, CreateView):
    pass


class TrainerUpdateView(TrainerFormMixin, UpdateView):
    pass


class SessionSeriesListView(StaffRequiredMixin, ListView):
    model = SessionSeries
    context_object_name = "series_list"
    template_name = "backoffice/session_series_list.html"

    def get_queryset(self):
        return SessionSeries.objects.select_related("catalog_type").prefetch_related(
            "trainers", "eligible_levels"
        )


class SessionSeriesFormMixin(StaffRequiredMixin):
    model = SessionSeries
    form_class = SessionSeriesForm
    template_name = "backoffice/session_series_form.html"

    def form_valid(self, form):
        if form.instance.created_by_id is None:
            form.instance.created_by = self.request.user
        response = super().form_valid(form)
        result = materialize_session_series(self.object)
        messages.success(
            self.request,
            _("Schedule saved: %(created)s sessions created and %(skipped)s closed days skipped.")
            % {"created": result.created, "skipped": result.skipped_closed},
        )
        return response

    def get_success_url(self):
        return reverse("backoffice:session_series")


class SessionSeriesCreateView(SessionSeriesFormMixin, CreateView):
    pass


class SessionSeriesUpdateView(SessionSeriesFormMixin, UpdateView):
    pass


class SessionSeriesMaterializeView(StaffRequiredMixin, View):
    def post(self, request, pk):
        series = get_object_or_404(SessionSeries, pk=pk)
        try:
            result = materialize_session_series(series)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                _(
                    "Calendar updated: %(created)s created, %(existing)s already existed, "
                    "%(closed)s closed days skipped and %(conflicts)s trainer conflicts skipped."
                )
                % {
                    "created": result.created,
                    "existing": result.existing,
                    "closed": result.skipped_closed,
                    "conflicts": result.trainer_conflicts,
                },
            )
        return redirect("backoffice:session_series")


class ClosureListView(StaffRequiredMixin, ListView):
    model = OperationalClosure
    context_object_name = "closures"
    template_name = "backoffice/closure_list.html"

    def get_queryset(self):
        return OperationalClosure.objects.prefetch_related("catalog_types")


class ClosureFormMixin(StaffRequiredMixin):
    model = OperationalClosure
    form_class = OperationalClosureForm
    template_name = "backoffice/closure_form.html"

    def form_valid(self, form):
        if form.instance.created_by_id is None:
            form.instance.created_by = self.request.user
        response = super().form_valid(form)
        impacted_sessions = ActivitySession.objects.filter(
            starts_at__lt=self.object.ends_at,
            ends_at__gt=self.object.starts_at,
        )
        if not self.object.all_operational_services:
            impacted_sessions = impacted_sessions.filter(
                catalog_type__in=self.object.catalog_types.all()
            )
        impacted_reservations = ActivityReservation.objects.filter(
            session__in=impacted_sessions,
            status__in=RESERVED_PLACE_STATUSES,
        ).count()
        messages.warning(
            self.request,
            _(
                "Closure saved. %(sessions)s existing sessions and %(reservations)s reservations "
                "need an explicit operational decision; none were silently changed."
            )
            % {
                "sessions": impacted_sessions.count(),
                "reservations": impacted_reservations,
            },
        )
        return response

    def get_success_url(self):
        return reverse("backoffice:closures")


class ClosureCreateView(ClosureFormMixin, CreateView):
    pass


class ClosureUpdateView(ClosureFormMixin, UpdateView):
    pass


class WeeklyUsageAdjustmentCreateView(StaffRequiredMixin, FormView):
    form_class = WeeklyUsageAdjustmentForm
    template_name = "backoffice/weekly_usage_adjustment_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.entitlement = get_object_or_404(
            ServiceEntitlement,
            pk=kwargs["pk"],
            offering_mode_snapshot=OfferingMode.SUBSCRIPTION,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["entitlement"] = self.entitlement
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["entitlement"] = self.entitlement
        return context

    def form_valid(self, form):
        adjustment = form.save(commit=False)
        adjustment.created_by = self.request.user
        adjustment.full_clean()
        adjustment.save()
        messages.success(self.request, _("Extra uses granted for the selected week."))
        return redirect("backoffice:subscription_detail", pk=self.entitlement.pk)


class CatalogListView(StaffRequiredMixin, TemplateView):
    """Catalog landing page: business hierarchy, not raw ERP records."""

    template_name = "backoffice/catalog_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "catalog_types": CatalogType.objects.prefetch_related(
                    "levels",
                    "offers__catalog_item",
                ).order_by("order", "name"),
                "sync_attention_count": CatalogItem.objects.filter(
                    sync_status__in=(CatalogSyncStatus.FAILED, CatalogSyncStatus.DIVERGED)
                ).count(),
                "pending_sync_count": CatalogItem.objects.filter(
                    sync_status=CatalogSyncStatus.PENDING,
                ).count(),
            }
        )
        return context


class CatalogOptionalServicesView(StaffRequiredMixin, FormView):
    """Manage reusable optional services from one catalog-level workspace."""

    form_class = OptionalServiceConfigurationForm
    template_name = "backoffice/catalog_optional_services.html"

    def get_initial(self):
        initial = super().get_initial()
        requested_target = self.request.GET.get("target", "").strip()
        if requested_target:
            initial["target"] = requested_target
        else:
            first_type = CatalogType.objects.order_by("order", "name", "pk").first()
            if first_type is not None:
                initial["target"] = f"type:{first_type.pk}"
        try:
            target_type, target_level = resolve_optional_service_target(initial.get("target"))
        except ValidationError:
            return initial
        initial["add_on_ids"] = AddOnAvailability.objects.filter(
            target_type=target_type,
            target_level=target_level,
            is_active=True,
        ).values_list("add_on_id", flat=True)
        return initial

    def form_valid(self, form):
        try:
            result = replace_optional_services(
                target_type=form.target_type,
                target_level=form.target_level,
                optional_service_ids=list(
                    form.cleaned_data["add_on_ids"].values_list("pk", flat=True)
                ),
                actor=self.request.user,
            )
        except InvalidOptionalServiceConfiguration:
            form.add_error(
                "add_on_ids",
                _("The optional-service selection is no longer valid. Refresh and try again."),
            )
            return self.form_invalid(form)

        logger.info(
            "catalog optional services updated",
            extra={
                "actor_id": self.request.user.pk,
                "catalog_type_id": form.target_type.pk,
                "catalog_level_id": getattr(form.target_level, "pk", None),
                "activated_count": result.activated,
                "deactivated_count": result.deactivated,
            },
        )
        if result.activated or result.deactivated:
            messages.success(self.request, _("Optional services updated."))
        else:
            messages.info(self.request, _("There were no optional-service changes to save."))
        query = urlencode({"target": form.cleaned_data["target"]})
        return redirect(f"{reverse('backoffice:catalog_optional_services')}?{query}")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        target_value = form.data.get("target") if form.is_bound else form.initial.get("target")
        try:
            target_type, target_level = resolve_optional_service_target(target_value)
        except ValidationError:
            target_type, target_level = None, None

        context.update(
            {
                "target_type": target_type,
                "target_level": target_level,
                "available_optional_services": (),
                "selected_optional_services": (),
                "inherited_optional_services": (),
            }
        )
        if target_type is None:
            return context

        direct_assignments = AddOnAvailability.objects.filter(
            target_type=target_type,
            target_level=target_level,
            is_active=True,
        )
        direct_ids = set(direct_assignments.values_list("add_on_id", flat=True))
        inherited_ids = set()
        if target_level is not None:
            inherited_ids = set(
                AddOnAvailability.objects.filter(
                    target_type=target_type,
                    target_level__isnull=True,
                    is_active=True,
                ).values_list("add_on_id", flat=True)
            )

        offers = optional_service_offers()
        available_offers = offers.exclude(catalog_type=target_type)
        context.update(
            {
                "selected_optional_services": offers.filter(pk__in=direct_ids),
                "inherited_optional_services": offers.filter(pk__in=inherited_ids),
                "available_optional_services": available_offers.exclude(
                    pk__in=direct_ids | inherited_ids
                ),
                "selected_service_count": len(direct_ids | inherited_ids),
            }
        )
        return context


class CatalogTypeDetailView(StaffRequiredMixin, DetailView):
    model = CatalogType
    context_object_name = "catalog_type"
    template_name = "backoffice/catalog_type_detail.html"

    def get_queryset(self):
        return CatalogType.objects.prefetch_related(
            "levels__offers__catalog_item",
            "offers__catalog_item",
            "available_add_ons__add_on__catalog_item",
            "available_add_ons__target_level",
        )


class CatalogTypeCreateView(StaffRequiredMixin, CreateView):
    model = CatalogType
    form_class = CatalogTypeForm
    template_name = "backoffice/catalog_type_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, _("Catalog type created."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("backoffice:catalog_type_detail", kwargs={"pk": self.object.pk})


class CatalogTypeUpdateView(StaffRequiredMixin, UpdateView):
    model = CatalogType
    form_class = CatalogTypeForm
    template_name = "backoffice/catalog_type_form.html"

    def form_valid(self, form):
        messages.success(self.request, _("Catalog type updated."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("backoffice:catalog_type_detail", kwargs={"pk": self.object.pk})


class CatalogLevelFormMixin(StaffRequiredMixin, FormView):
    form_class = CatalogLevelForm
    template_name = "backoffice/catalog_level_form.html"
    level = None

    def dispatch(self, request, *args, **kwargs):
        self.catalog_type = get_object_or_404(CatalogType, pk=kwargs["type_pk"])
        if "pk" in kwargs:
            self.level = get_object_or_404(
                CatalogLevel,
                pk=kwargs["pk"],
                catalog_type=self.catalog_type,
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({"catalog_type": self.catalog_type, "instance": self.level})
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"catalog_type": self.catalog_type, "level": self.level})
        return context

    def form_valid(self, form):
        level = form.save(commit=False)
        if level.created_by_id is None:
            level.created_by = self.request.user
        level.save()
        messages.success(
            self.request,
            _("Catalog level updated.") if self.level else _("Catalog level created."),
        )
        return redirect("backoffice:catalog_type_detail", pk=self.catalog_type.pk)


class CatalogLevelCreateView(CatalogLevelFormMixin):
    pass


class CatalogLevelUpdateView(CatalogLevelFormMixin):
    pass


class CatalogLevelOrderView(StaffRequiredMixin, View):
    """Persist the complete visual order of a catalog type's levels."""

    http_method_names = ("post",)

    def post(self, request, *args, **kwargs):
        catalog_type = get_object_or_404(CatalogType, pk=kwargs["type_pk"])
        try:
            payload = json.loads(request.body)
            ordered_level_ids = payload["level_ids"]
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            return JsonResponse({"error": _("Invalid level order.")}, status=400)

        if not isinstance(ordered_level_ids, list) or any(
            isinstance(level_id, bool) or not isinstance(level_id, int)
            for level_id in ordered_level_ids
        ):
            return JsonResponse({"error": _("Invalid level order.")}, status=400)

        try:
            reorder_catalog_levels(
                catalog_type=catalog_type,
                ordered_level_ids=ordered_level_ids,
            )
        except InvalidCatalogLevelOrder:
            return JsonResponse({"error": _("Invalid level order.")}, status=400)

        logger.info(
            "catalog levels reordered",
            extra={
                "actor_id": request.user.pk,
                "catalog_type_id": catalog_type.pk,
                "catalog_level_ids": ordered_level_ids,
            },
        )
        return JsonResponse({"message": _("Catalog level order saved.")})


class CatalogOfferFormMixin(StaffRequiredMixin, FormView):
    form_class = CatalogOfferForm
    template_name = "backoffice/catalog_offer_form.html"
    offer = None

    def dispatch(self, request, *args, **kwargs):
        self.catalog_type = get_object_or_404(CatalogType, pk=kwargs["type_pk"])
        if "pk" in kwargs:
            self.offer = get_object_or_404(
                CatalogOffer.objects.select_related("catalog_item", "level"),
                pk=kwargs["pk"],
                catalog_type=self.catalog_type,
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({"catalog_type": self.catalog_type, "instance": self.offer})
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if not self.offer and self.request.GET.get("level"):
            initial["level"] = self.request.GET["level"]
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "catalog_type": self.catalog_type,
                "offer": self.offer,
                "catalog_versions": (
                    catalog_version_history(self.offer.catalog_item) if self.offer else ()
                ),
            }
        )
        return context

    def form_valid(self, form):
        offer = form.save()
        if offer.created_by_id is None:
            offer.created_by = self.request.user
            offer.save(update_fields=("created_by", "updated_at"))
        messages.success(
            self.request,
            _("Price or plan updated and queued for TOConline synchronization.")
            if self.offer
            else _("Price or plan created and queued for TOConline synchronization."),
        )
        return redirect("backoffice:catalog_type_detail", pk=self.catalog_type.pk)


class CatalogOfferCreateView(CatalogOfferFormMixin):
    pass


class CatalogOfferUpdateView(CatalogOfferFormMixin):
    pass


class CatalogProjectionListView(StaffRequiredMixin, ListView):
    template_name = "backoffice/catalog_projection_list.html"
    context_object_name = "catalog_items"
    paginate_by = 40

    def get_queryset(self):
        queryset = CatalogItem.objects.select_related("catalog_offer").order_by(
            "family_name", "name", "id"
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(item_code__icontains=query)
                | Q(toconline_id__icontains=query)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "").strip()
        return context


class CatalogSyncView(StaffRequiredMixin, View):
    def post(self, request):
        try:
            result = TocOnlineCatalogSynchronizer().synchronize()
        except CatalogSyncError as exc:
            logger.warning("TOConline catalog synchronization rejected: %s", exc)
            messages.error(request, _("The TOConline catalog could not be synchronized."))
        except Exception:
            logger.exception("Unexpected TOConline catalog synchronization failure")
            messages.error(request, _("The TOConline catalog could not be synchronized."))
        else:
            messages.success(
                request,
                _(
                    "TOConline synchronized: %(published)s published, %(created)s imported, "
                    "%(updated)s refreshed, %(deactivated)s unavailable and "
                    "%(diverged)s divergent."
                )
                % {
                    "created": result.created,
                    "updated": result.updated,
                    "deactivated": result.deactivated,
                    "published": result.published,
                    "diverged": result.diverged,
                },
            )
        return redirect("backoffice:catalog")


class ActivityReservationStatusView(StaffRequiredMixin, View):
    allowed_statuses = {
        ReservationStatus.ATTENDED,
        ReservationStatus.NO_SHOW,
        ReservationStatus.BOOKED,
    }

    def post(self, request, pk):
        reservation = get_object_or_404(ActivityReservation, pk=pk)
        status = request.POST.get("status")
        if status not in self.allowed_statuses or reservation.status in {
            ReservationStatus.CANCELLED,
            ReservationStatus.LATE_CANCELLED,
        }:
            messages.error(request, _("That reservation status change is not allowed."))
        else:
            try:
                set_reservation_attendance(
                    reservation,
                    status=status,
                    actor=request.user,
                )
            except ActivityReservationError as exc:
                messages.error(request, exc.messages[0])
            else:
                messages.success(request, _("Reservation status updated."))
        return redirect(
            _safe_return_url(
                request,
                request.POST.get("next"),
                fallback=reverse("backoffice:activities_dashboard"),
            )
        )


class ActivityReservationCancelView(StaffRequiredMixin, View):
    template_name = "backoffice/activity_reservation_cancel.html"

    def get(self, request, pk):
        reservation = self._reservation(pk)
        return render(
            request,
            self.template_name,
            {
                "reservation": reservation,
                "form": ActivityReservationCancellationForm(),
                "return_url": self._return_url(request),
            },
        )

    def post(self, request, pk):
        reservation = self._reservation(pk)
        form = ActivityReservationCancellationForm(request.POST)
        return_url = self._return_url(request)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "reservation": reservation,
                    "form": form,
                    "return_url": return_url,
                },
            )
        try:
            cancelled, credit_returned = cancel_reservation(
                reservation,
                actor=request.user,
                override_policy=form.cleaned_data["restore_entitlement"],
                reason=form.cleaned_data["reason"],
            )
        except ActivityReservationError as exc:
            form.add_error(None, exc.messages[0])
            return render(
                request,
                self.template_name,
                {
                    "reservation": reservation,
                    "form": form,
                    "return_url": return_url,
                },
            )
        if credit_returned:
            messages.success(request, _("Reservation cancelled and the use was returned."))
        elif cancelled.status == ReservationStatus.LATE_CANCELLED:
            messages.success(
                request,
                _("Reservation cancelled after the deadline without returning the use."),
            )
        else:
            messages.success(request, _("Reservation cancelled."))
        return redirect(return_url)

    @staticmethod
    def _reservation(pk):
        return get_object_or_404(
            ActivityReservation.objects.select_related(
                "session",
                "pet",
                "entitlement__customer__user",
            ),
            pk=pk,
            status=ReservationStatus.BOOKED,
        )

    @staticmethod
    def _return_url(request):
        return _safe_return_url(
            request,
            request.POST.get("next") or request.GET.get("next"),
            fallback=reverse("backoffice:activities_dashboard"),
        )


class DashboardView(StaffRequiredMixin, TemplateView):
    template_name = "backoffice/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        form = BookingStayFilterForm(self.request.GET or None)
        filters = form.cleaned_data if form.is_valid() else {}
        stays = filtered_booking_stays(filters)
        paginator = Paginator(stays, 25)
        page_obj = paginator.get_page(self.request.GET.get("page"))

        active = operational_stays(today)
        checkins = BookingStay.objects.filter(
            booking__status__in=hotel_models.CONFIRMED_BOOKING_STATUSES,
            start_date=today,
        )
        checkouts = BookingStay.objects.filter(
            booking__status__in=hotel_models.CONFIRMED_BOOKING_STATUSES,
            end_date=today,
        )
        medication_count = active.exclude(pet__medication="").count()
        payment_attention_count = hotel_models.PaymentAttempt.objects.filter(
            status__in=(
                hotel_models.PaymentStatus.FAILED,
                hotel_models.PaymentStatus.EXPIRED,
                hotel_models.PaymentStatus.REFUND_FAILED,
                hotel_models.PaymentStatus.DISPUTED,
            )
        ).count()
        document_attention_count = hotel_models.Booking.objects.filter(
            document_status=hotel_models.BookingDocumentStatus.FAILED,
        ).count()
        integration_attention_count = (
            CatalogItem.objects.filter(
                sync_status__in=(CatalogSyncStatus.FAILED, CatalogSyncStatus.DIVERGED)
            ).count()
            + CustomerProfile.objects.filter(
                toconline_sync_status=CustomerSyncStatus.FAILED
            ).count()
            + ServiceEntitlement.objects.filter(
                document_status=hotel_models.BookingDocumentStatus.FAILED
            ).count()
            + SubscriptionCycle.objects.filter(
                document_status=hotel_models.BookingDocumentStatus.FAILED
            ).count()
        )
        stats = {
            "checkins_count": checkins.count(),
            "checkouts_count": checkouts.count(),
            "active_count": active.count(),
            "medication_count": medication_count,
        }

        context.update(
            {
                "filter_form": form,
                "checkins": checkins,
                "checkouts": checkouts,
                "active_stays": active,
                "stays": page_obj.object_list,
                "page_obj": page_obj,
                "paginator": paginator,
                "is_paginated": page_obj.has_other_pages(),
                "stats": stats,
                "payment_attention_count": payment_attention_count,
                "document_attention_count": document_attention_count,
                "integration_attention_count": integration_attention_count,
                "today": today,
            }
        )
        return context


class UsersListView(StaffRequiredMixin, ListView):
    template_name = "backoffice/users_list.html"
    context_object_name = "users"
    paginate_by = 20

    def get_queryset(self):
        User = get_user_model()
        queryset = User.objects.select_related("customer_profile").filter(is_staff=False)
        self.filter_form = CustomerListFilterForm(self.request.GET or None)
        if self.filter_form.is_valid():
            filters = self.filter_form.cleaned_data
            if filters.get("q"):
                query = filters["q"]
                queryset = queryset.filter(
                    Q(first_name__icontains=query)
                    | Q(last_name__icontains=query)
                    | Q(username__icontains=query)
                    | Q(email__icontains=query)
                    | Q(customer_profile__billing_phone__icontains=query)
                    | Q(customer_profile__tax_number__icontains=query)
                )
            if filters.get("status") == "active":
                queryset = queryset.filter(is_active=True)
            elif filters.get("status") == "inactive":
                queryset = queryset.filter(is_active=False)
            elif filters.get("status") == "invited":
                queryset = queryset.filter(password__startswith="!")
        return queryset.order_by("first_name", "last_name", "username")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class UserCreateView(StaffRequiredMixin, FormView):
    template_name = "backoffice/user_create.html"
    form_class = CustomerInvitationForm

    def form_valid(self, form):
        user = form.save()
        try:
            send_customer_invitation(user)
        except Exception:
            messages.warning(
                self.request,
                _("The customer was created, but the invitation email could not be sent."),
            )
        else:
            messages.success(self.request, _("Customer created and invitation sent."))

        return redirect("backoffice:user_detail", pk=user.pk)


class CustomerInvitationResendView(StaffRequiredMixin, View):
    def post(self, request, pk):
        User = get_user_model()
        user = get_object_or_404(
            User.objects.select_related("customer_profile"),
            pk=pk,
            is_staff=False,
        )
        if user.has_usable_password():
            messages.info(request, _("This customer has already created a password."))
        else:
            try:
                send_customer_invitation(user)
            except Exception:
                messages.error(request, _("The invitation email could not be sent."))
            else:
                messages.success(request, _("Invitation sent."))
        return redirect("backoffice:user_detail", pk=user.pk)


class UserDetailMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        user_pk = self.kwargs.get("pk", self.request.user.pk)
        return self.request.user.is_staff or self.request.user.pk == user_pk

    def get_queryset(self):
        User = get_user_model()
        return User.objects.select_related("customer_profile")

    def get_object(self, queryset=None):
        User = get_user_model()
        user_pk = self.kwargs.get("pk", self.request.user.pk)

        return User.objects.select_related("customer_profile").get(pk=user_pk)


class UserDetailView(UserDetailMixin, DetailView):
    template_name = "backoffice/user_detail.html"
    context_object_name = "user_obj"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.object
        bookings_qs = (
            hotel_models.Booking.objects.filter(customer__user=user)
            .prefetch_related("stays")
            .order_by("-created_at")
        )

        paginator = Paginator(bookings_qs, 10)
        page_number = self.request.GET.get("page")
        bookings_page = paginator.get_page(page_number)

        user_pets = Pet.objects.select_related("species").filter(owner__user=user).order_by("name")
        activity_entitlements = (
            ServiceEntitlement.objects.filter(customer__user=user)
            .select_related(
                "pet",
                "offering__catalog_offer__catalog_item",
                "offering__catalog_offer__catalog_type",
                "offering__catalog_offer__level",
            )
            .annotate(
                credit_balance_value=Coalesce(
                    Sum("credit_entries__delta"),
                    Value(0),
                    output_field=IntegerField(),
                )
            )
        )

        context.update(
            {
                "customer_profile": getattr(user, "customer_profile", None),
                "bookings": bookings_page.object_list,
                "page_obj": bookings_page,
                "paginator": paginator,
                "is_paginated": bookings_page.has_other_pages(),
                "user_pets": user_pets,
                "activity_entitlements": activity_entitlements,
                "current_date": timezone.now().date(),
            }
        )
        return context


class UserUpdateView(UserDetailMixin, UpdateView):
    template_name = "backoffice/user_form.html"
    form_class = UserChangeForm

    def get_success_url(self):
        return _safe_return_url(
            self.request,
            self.request.GET.get("next") or self.request.POST.get("next"),
            fallback=reverse("backoffice:user_detail", kwargs={"pk": self.object.pk}),
        )


class CustomerPasswordSetView(StaffRequiredMixin, FormView):
    form_class = SetPasswordForm
    template_name = "backoffice/customer_password_form.html"

    def get_target_user(self):
        if not hasattr(self, "target_user"):
            self.target_user = get_object_or_404(
                get_user_model(),
                pk=self.kwargs["pk"],
                is_staff=False,
            )
        return self.target_user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.get_target_user()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["user_obj"] = self.get_target_user()
        return context

    def form_valid(self, form):
        target_user = form.save()
        logger.info(
            "Customer password set by staff",
            extra={
                "actor_user_id": self.request.user.pk,
                "target_user_id": target_user.pk,
            },
        )
        messages.success(self.request, _("The customer's password was updated."))
        return redirect("backoffice:user_detail", pk=target_user.pk)


class LegalDocumentListView(StaffRequiredMixin, ListView):
    queryset = LegalDocument.objects.filter(deleted_at__isnull=True).all()
    context_object_name = "legal_documents"


class LegalDocumentCreateOrUpdateView(StaffRequiredMixin, TemplateView):
    template_name = "backoffice/legaldocument_form.html"
    success_url = reverse_lazy("backoffice:legal_documents")

    def get(self, request, pk=None):
        document = self._get_document(pk)
        return render(request, self.template_name, self._build_context(document=document))

    def post(self, request, pk=None):
        document = self._get_document(pk)
        context = self._build_context(data=request.POST, document=document)
        doc_form = context["doc_form"]
        section_formset = context["section_formset"]
        section_rows = context["section_rows"]

        document_is_valid = doc_form.is_valid()
        sections_are_valid = section_formset.is_valid()
        clauses_are_valid = self._validate_active_clause_formsets(
            section_rows,
            sections_are_valid=sections_are_valid,
        )

        if not all((document_is_valid, sections_are_valid, clauses_are_valid)):
            return render(request, self.template_name, context)

        with transaction.atomic():
            self._normalize_document_order(section_rows)
            doc_form.save()
            section_formset.save()
            for section_form, clause_formset in section_rows:
                if section_form.cleaned_data.get("DELETE"):
                    continue
                clause_formset.instance = section_form.instance
                clause_formset.save()

        message = _("Legal document created.") if pk is None else _("Legal document updated.")
        messages.success(self.request, message)

        return redirect(self.success_url)

    @staticmethod
    def _get_document(pk):
        if pk is None:
            return None
        return get_object_or_404(LegalDocument.objects_active, pk=pk)

    @staticmethod
    def _validate_active_clause_formsets(section_rows, *, sections_are_valid):
        if not sections_are_valid:
            for _, clause_formset in section_rows:
                clause_formset.is_valid()
            return False

        clause_formset_results = [
            clause_formset.is_valid()
            for section_form, clause_formset in section_rows
            if not section_form.cleaned_data.get("DELETE")
        ]
        return all(clause_formset_results)

    @staticmethod
    def _normalize_document_order(section_rows):
        active_section_order = 0
        for section_form, clause_formset in section_rows:
            if section_form.cleaned_data.get("DELETE"):
                continue

            section_form.cleaned_data["order"] = active_section_order
            section_form.instance.order = active_section_order
            active_section_order += 1

            active_clause_order = 0
            for clause_form in clause_formset.forms:
                if clause_form.cleaned_data.get("DELETE"):
                    continue
                clause_form.cleaned_data["order"] = active_clause_order
                clause_form.instance.order = active_clause_order
                active_clause_order += 1

    @staticmethod
    def _build_context(*, document, data=None):
        doc_form = LegalDocumentForm(data, instance=document)
        section_formset_class = create_section_formset()
        clause_formset_class = create_lineitem_formset()
        section_formset = section_formset_class(
            data,
            instance=doc_form.instance,
            prefix="sections",
        )
        clause_formsets = [
            clause_formset_class(
                data,
                instance=section_form.instance,
                prefix=f"lineitems-{index}",
            )
            for index, section_form in enumerate(section_formset)
        ]
        empty_clause_form = clause_formset_class(
            instance=LegalDocumentSection(),
            prefix="lineitems-__section__",
        ).empty_form

        return {
            "object": document,
            "doc_form": doc_form,
            "section_formset": section_formset,
            "section_rows": list(zip(section_formset.forms, clause_formsets, strict=True)),
            "empty_clause_form": empty_clause_form,
        }


class LegalDocumentDeleteView(StaffRequiredMixin, DeleteView):
    queryset = LegalDocument.objects.all()
    success_url = reverse_lazy("backoffice:legal_documents")

    def form_valid(self, form):
        success_url = self.get_success_url()

        self.object.deleted_at = timezone.now()
        self.object.save()

        messages.success(self.request, _("Legal Document deleted."))

        return HttpResponseRedirect(success_url)


class ContactRequestListView(StaffRequiredMixin, ListView):
    model = ContactRequest
    context_object_name = "contacts_requests"
    template_name = "backoffice/contactrequest_list.html"
    paginate_by = 10


class ContactRequestMarkReadView(StaffRequiredMixin, View):
    def post(self, request):
        ids = request.POST.getlist("selected_requests")

        if ids:
            updated = ContactRequest.objects.filter(id__in=ids, read_at__isnull=True)
            updated_count = updated.update(read_at=timezone.now())
            if updated_count:
                messages.success(request, _("Marked as read."))
            else:
                messages.info(request, _("No unread requests selected."))
        else:
            messages.warning(request, _("No requests selected."))

        return redirect("backoffice:contacts_requests")


class UserToggleActiveView(StaffRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_authenticated or not request.user.is_staff:
            messages.error(request, _("Unauthorized"))
            return redirect("backoffice:dashboard")

        User = get_user_model()
        target = User.objects.filter(pk=pk).first()
        if not target:
            messages.error(request, _("User not found"))
            return redirect("backoffice:users_list")

        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])

        msg = _("User deactivated") if not target.is_active else _("User activated")
        messages.success(request, msg)

        next_url = _safe_return_url(
            request,
            request.POST.get("next"),
            request.META.get("HTTP_REFERER"),
            fallback=reverse("backoffice:user_detail", kwargs={"pk": pk}),
        )

        return redirect(next_url)


class AssistedBookingStartView(StaffRequiredMixin, View):
    destinations = {
        "hotel": "hotel:booking_stay",
        "activities": "activities:offering_list",
    }

    def post(self, request, pk):
        if not request.user.is_authenticated or not request.user.is_staff:
            messages.error(request, _("Unauthorized"))
            return redirect("backoffice:dashboard")

        User = get_user_model()
        target = (
            User.objects.select_related("customer_profile")
            .filter(pk=pk, is_active=True, is_staff=False)
            .first()
        )
        customer_profile = getattr(target, "customer_profile", None) if target else None
        if customer_profile is None:
            messages.error(request, _("This user is not an active customer."))
            return redirect("backoffice:users_list")

        request.session["assisted_customer_id"] = customer_profile.pk
        request.session.pop("booking_data", None)

        messages.info(request, _("You are now assisting the selected customer."))
        destination = self.destinations.get(request.POST.get("destination"), "hotel:booking_stay")
        return redirect(destination)


class AssistedBookingStopView(StaffRequiredMixin, View):
    def post(self, request):
        request.session.pop("assisted_customer_id", None)
        request.session.pop("booking_data", None)
        messages.info(request, _("Assisted booking mode ended."))
        return redirect("backoffice:users_list")


def _safe_return_url(request, *candidates, fallback):
    allowed_hosts = {request.get_host()}
    for candidate in candidates:
        if candidate and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts=allowed_hosts,
            require_https=request.is_secure(),
        ):
            return candidate
    return fallback
