import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView

from accounts.models import CustomerProfile
from activities.forms import (
    CancelFlexibleServiceForm,
    FlexibleServiceRequestForm,
    PurchaseServiceForm,
    ReserveSessionForm,
)
from activities.models import (
    RESERVED_PLACE_STATUSES,
    ActivityReservation,
    ActivitySession,
    EntitlementRefund,
    EntitlementStatus,
    ReservationStatus,
    ServiceEntitlement,
    ServiceFulfilment,
    ServiceFulfilmentStatus,
    ServiceOffering,
    SubscriptionCycle,
)
from activities.services.add_ons import (
    ActivityAddOnError,
    available_activity_add_ons,
    validate_activity_add_on_purchase,
)
from activities.services.billing import (
    cancel_subscription,
    create_entitlement,
    send_entitlement_payment_link_email,
)
from activities.services.calendar import closure_reason
from activities.services.discounts import preview_activity_promotion
from activities.services.fulfilments import (
    ServiceFulfilmentError,
    cancel_fulfilment,
    request_entitlement_fulfilment,
)
from activities.services.reservations import (
    ActivityReservationError,
    cancel_reservation,
    reserve_session,
)
from catalog.models import CatalogWorkflow
from discounts.services import PromotionUnavailable
from hotel.models import ServiceSchedulePreference
from hotel.services.payments import PaymentGatewayError
from olhocao.integrations.toconline import toconline_gateway

logger = logging.getLogger(__name__)


def with_credit_balance(queryset):
    return queryset.annotate(
        credit_balance_value=Coalesce(
            Sum("credit_entries__delta"),
            Value(0),
            output_field=IntegerField(),
        )
    )


def get_activity_customer(request):
    if request.user.is_staff:
        customer_id = request.session.get("assisted_customer_id")
        if not customer_id:
            return None
        return CustomerProfile.objects.filter(
            pk=customer_id,
            user__is_active=True,
            user__is_staff=False,
        ).first()
    return getattr(request.user, "customer_profile", None)


class OfferingListView(ListView):
    template_name = "activities/offering_list.html"
    context_object_name = "offerings"

    def get_queryset(self):
        queryset = (
            ServiceOffering.objects.select_related(
                "catalog_offer__catalog_item",
                "catalog_offer__catalog_type",
                "catalog_offer__level",
            )
            .filter(
                is_published=True,
                catalog_offer__is_active=True,
                catalog_offer__is_public=True,
                catalog_offer__is_standalone=True,
                catalog_offer__catalog_item__is_active=True,
                catalog_offer__catalog_type__is_active=True,
            )
            .filter(
                Q(catalog_offer__catalog_item__origin="website")
                | Q(
                    catalog_offer__catalog_item__remote_deleted_at__isnull=True,
                    catalog_offer__catalog_item__toconline_id__isnull=False,
                )
            )
        )
        type_slug = self.request.GET.get("type", "").strip()
        if type_slug:
            queryset = queryset.filter(catalog_offer__catalog_type__slug=type_slug)
        return queryset


class CustomerRequiredMixin(LoginRequiredMixin):
    customer = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.customer = get_activity_customer(request)
        if self.customer is None:
            if request.user.is_staff:
                messages.info(request, _("Select a customer before managing an activity."))
                return redirect("backoffice:users_list")
            messages.error(request, _("Your customer profile is incomplete."))
            return redirect("frontoffice:home")
        return super().dispatch(request, *args, **kwargs)


class EntitlementCheckoutMixin:
    """Shared controller flow for standalone and reservation add-on checkout."""

    def complete_checkout(
        self,
        form,
        *,
        pet,
        schedule_preference="unscheduled",
        preferred_date=None,
        preferred_time=None,
        customer_notes="",
        source_activity_reservation=None,
    ):
        if self.request.POST.get("action") == "preview_promotion":
            try:
                quote = preview_activity_promotion(
                    offering=self.offering,
                    pet=pet,
                    customer=self.customer,
                    code=form.cleaned_data["promotion_code"],
                    source_activity_reservation=source_activity_reservation,
                )
            except PromotionUnavailable as exc:
                form.add_error("promotion_code", str(exc))
                return self.form_invalid(form)
            context = self.get_context_data(form=form)
            context["promotion_quote"] = quote
            return self.render_to_response(context)
        try:
            entitlement = create_entitlement(
                customer=self.customer,
                pet=pet,
                offering=self.offering,
                actor=self.request.user,
                notify_customer=not self.request.user.is_staff,
                promotion_code=form.cleaned_data["promotion_code"],
                schedule_preference=schedule_preference,
                preferred_date=preferred_date,
                preferred_time=preferred_time,
                customer_notes=customer_notes,
                source_activity_reservation=source_activity_reservation,
            )
        except PaymentGatewayError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        if self.request.user.is_staff:
            try:
                send_entitlement_payment_link_email(entitlement)
            except PaymentGatewayError as exc:
                form.add_error(None, str(exc))
                return self.form_invalid(form)
            customer_user_id = self.customer.user_id
            self.request.session.pop("assisted_customer_id", None)
            messages.success(
                self.request,
                _("The payment link was sent to the customer by email."),
            )
            return redirect("backoffice:user_detail", pk=customer_user_id)
        return redirect(entitlement.payment_url)


class PurchaseServiceView(EntitlementCheckoutMixin, CustomerRequiredMixin, FormView):
    template_name = "activities/purchase.html"
    form_class = PurchaseServiceForm

    def dispatch(self, request, *args, **kwargs):
        self.offering = get_object_or_404(
            ServiceOffering.objects.select_related(
                "catalog_offer__catalog_item",
                "catalog_offer__catalog_type",
                "catalog_offer__level",
            ).filter(
                Q(catalog_offer__catalog_item__origin="website")
                | Q(
                    catalog_offer__catalog_item__remote_deleted_at__isnull=True,
                    catalog_offer__catalog_item__toconline_id__isnull=False,
                )
            ),
            pk=kwargs["pk"],
            is_published=True,
            catalog_offer__is_active=True,
            catalog_offer__is_public=True,
            catalog_offer__is_standalone=True,
            catalog_offer__catalog_item__is_active=True,
            catalog_offer__catalog_type__is_active=True,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(customer=self.customer, offering=self.offering)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["offering"] = self.offering
        return context

    def form_valid(self, form):
        return self.complete_checkout(
            form,
            pet=form.cleaned_data["pet"],
            schedule_preference=form.cleaned_data.get(
                "schedule_preference", ServiceSchedulePreference.UNSCHEDULED
            ),
            preferred_date=form.cleaned_data.get("preferred_date"),
            preferred_time=form.cleaned_data.get("preferred_time"),
            customer_notes=form.cleaned_data.get("customer_notes", ""),
        )


class ActivityAddOnListView(CustomerRequiredMixin, DetailView):
    template_name = "activities/add_on_list.html"
    context_object_name = "reservation"

    def get_queryset(self):
        return ActivityReservation.objects.filter(
            entitlement__customer=self.customer,
            status=ReservationStatus.BOOKED,
        ).select_related(
            "session__catalog_type",
            "entitlement__offering__catalog_offer__level",
            "pet",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        already_attached = self.object.add_on_entitlements.filter(
            status__in=(
                EntitlementStatus.PENDING,
                EntitlementStatus.ACTIVE,
                EntitlementStatus.PAST_DUE,
                EntitlementStatus.CANCEL_AT_PERIOD_END,
                EntitlementStatus.SUSPENDED,
            )
        ).values_list("offering_id", flat=True)
        context["offerings"] = available_activity_add_ons(self.object).exclude(
            pk__in=already_attached
        )
        return context


class ActivityAddOnPurchaseView(
    EntitlementCheckoutMixin,
    CustomerRequiredMixin,
    FormView,
):
    template_name = "activities/purchase.html"
    form_class = PurchaseServiceForm

    def _load_purchase_context(self, reservation_pk, offering_pk):
        self.reservation = get_object_or_404(
            ActivityReservation.objects.select_related(
                "session__catalog_type",
                "entitlement__customer",
                "entitlement__offering__catalog_offer__level",
                "pet",
            ),
            pk=reservation_pk,
            entitlement__customer=self.customer,
            status=ReservationStatus.BOOKED,
        )
        self.offering = get_object_or_404(
            available_activity_add_ons(self.reservation),
            pk=offering_pk,
        )
        try:
            validate_activity_add_on_purchase(
                reservation=self.reservation,
                offering=self.offering,
                customer=self.customer,
            )
        except ActivityAddOnError as exc:
            raise Http404 from exc

    def get(self, request, *args, **kwargs):
        self._load_purchase_context(kwargs["reservation_pk"], kwargs["offering_pk"])
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self._load_purchase_context(kwargs["reservation_pk"], kwargs["offering_pk"])
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(
            customer=self.customer,
            offering=self.offering,
            fixed_pet=self.reservation.pet,
            collect_schedule_preference=False,
        )
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["offering"] = self.offering
        context["activity_reservation"] = self.reservation
        return context

    def form_valid(self, form):
        session_date = timezone.localtime(self.reservation.session.starts_at).date()
        return self.complete_checkout(
            form,
            pet=self.reservation.pet,
            schedule_preference=ServiceSchedulePreference.DATE,
            preferred_date=session_date,
            customer_notes=form.cleaned_data.get("customer_notes", ""),
            source_activity_reservation=self.reservation,
        )


class ActivityPortalView(CustomerRequiredMixin, TemplateView):
    template_name = "activities/portal.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["entitlements"] = with_credit_balance(
            ServiceEntitlement.objects.filter(customer=self.customer).select_related(
                "pet",
                "offering__catalog_offer__catalog_item",
                "offering__catalog_offer__catalog_type",
                "offering__catalog_offer__level",
            )
        )
        context["reservations"] = ActivityReservation.objects.filter(
            entitlement__customer=self.customer,
            status=ReservationStatus.BOOKED,
            session__starts_at__gte=timezone.now(),
        ).select_related("session", "pet", "entitlement")[:10]
        context["fulfilments"] = (
            ServiceFulfilment.objects.filter(
                Q(entitlement__customer=self.customer)
                | Q(booking_service__stay__booking__customer=self.customer)
            )
            .exclude(status=ServiceFulfilmentStatus.AWAITING_PAYMENT)
            .select_related(
                "pet",
                "entitlement__offering__catalog_offer",
                "booking_service__catalog_offer",
                "booking_service__stay__booking",
            )
            .distinct()[:20]
        )
        return context


class FlexibleServiceRequestView(CustomerRequiredMixin, FormView):
    template_name = "activities/fulfilment_request.html"
    form_class = FlexibleServiceRequestForm

    def dispatch(self, request, *args, **kwargs):
        self.entitlement = get_object_or_404(
            ServiceEntitlement.objects.select_related(
                "offering__catalog_offer__catalog_type",
                "pet",
            ),
            pk=kwargs["pk"],
            customer=get_activity_customer(request),
        )
        workflow = self.entitlement.offering.catalog_offer.catalog_type.workflow
        if workflow != CatalogWorkflow.FLEXIBLE:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["entitlement"] = self.entitlement
        return context

    def form_valid(self, form):
        try:
            request_entitlement_fulfilment(
                self.entitlement,
                schedule_preference=form.cleaned_data["schedule_preference"],
                preferred_date=form.cleaned_data.get("preferred_date"),
                preferred_time=form.cleaned_data.get("preferred_time"),
                customer_notes=form.cleaned_data.get("customer_notes", ""),
                actor=self.request.user,
            )
        except ServiceFulfilmentError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        messages.success(self.request, _("The service request was sent to the team."))
        return redirect("activities:portal")


class CancelFlexibleServiceView(CustomerRequiredMixin, FormView):
    template_name = "activities/fulfilment_cancel.html"
    form_class = CancelFlexibleServiceForm

    def dispatch(self, request, *args, **kwargs):
        self.fulfilment = get_object_or_404(
            ServiceFulfilment.objects.filter(
                Q(entitlement__customer=get_activity_customer(request))
                | Q(booking_service__stay__booking__customer=get_activity_customer(request))
            ).distinct(),
            pk=kwargs["pk"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["fulfilment"] = self.fulfilment
        return context

    def form_valid(self, form):
        try:
            _, credit_returned = cancel_fulfilment(
                self.fulfilment,
                actor=self.request.user,
                reason=form.cleaned_data["reason"],
            )
        except ServiceFulfilmentError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        if credit_returned:
            messages.success(self.request, _("Service cancelled and the pack use was returned."))
        else:
            messages.success(self.request, _("Service cancelled."))
        return redirect("activities:portal")


class ActivityDocumentListView(CustomerRequiredMixin, TemplateView):
    template_name = "activities/documents.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["purchase_documents"] = (
            ServiceEntitlement.objects.filter(
                customer=self.customer,
                offering_mode_snapshot__in=("one_off", "pack"),
            )
            .exclude(toconline_sale_document_id="")
            .select_related("pet", "offering")
        )
        context["purchase_refund_documents"] = (
            EntitlementRefund.objects.filter(
                entitlement__customer=self.customer,
                status="refunded",
            )
            .exclude(toconline_amend_document_id="")
            .select_related("entitlement__pet", "entitlement__offering")
        )
        context["subscription_cycles"] = (
            SubscriptionCycle.objects.filter(
                entitlement__customer=self.customer,
            )
            .exclude(toconline_sale_document_id="")
            .select_related(
                "entitlement__pet",
                "entitlement__offering",
            )
        )
        return context


class ActivityDocumentDownloadView(LoginRequiredMixin, View):
    document_field = "toconline_sale_document_id"

    def get(self, request, pk):
        cycle = get_object_or_404(
            SubscriptionCycle.objects.select_related("entitlement__customer"),
            pk=pk,
        )
        if not request.user.is_staff and cycle.entitlement.customer.user_id != request.user.pk:
            raise Http404
        document_id = getattr(cycle, self.document_field)
        if not document_id:
            raise Http404
        return _download_activity_document(
            request,
            document_id=document_id,
            filename=f"olhocao-subscription-{cycle.pk}.pdf",
            entitlement_id=cycle.entitlement_id,
            customer_user_id=cycle.entitlement.customer.user_id,
        )


class ActivityCreditNoteDownloadView(ActivityDocumentDownloadView):
    document_field = "toconline_amend_document_id"


class PurchaseDocumentDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        entitlement = get_object_or_404(
            ServiceEntitlement.objects.select_related("customer"),
            pk=pk,
            offering_mode_snapshot__in=("one_off", "pack"),
        )
        if not request.user.is_staff and entitlement.customer.user_id != request.user.pk:
            raise Http404
        if not entitlement.toconline_sale_document_id:
            raise Http404
        return _download_activity_document(
            request,
            document_id=entitlement.toconline_sale_document_id,
            filename=f"olhocao-service-{entitlement.pk}.pdf",
            entitlement_id=entitlement.pk,
            customer_user_id=entitlement.customer.user_id,
        )


class PurchaseRefundDocumentDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        refund = get_object_or_404(
            EntitlementRefund.objects.select_related("entitlement__customer"),
            pk=pk,
            status="refunded",
        )
        entitlement = refund.entitlement
        if not request.user.is_staff and entitlement.customer.user_id != request.user.pk:
            raise Http404
        if not refund.toconline_amend_document_id:
            raise Http404
        return _download_activity_document(
            request,
            document_id=refund.toconline_amend_document_id,
            filename=f"olhocao-service-refund-{refund.pk}.pdf",
            entitlement_id=entitlement.pk,
            customer_user_id=entitlement.customer.user_id,
        )


def _download_activity_document(
    request,
    *,
    document_id,
    filename,
    entitlement_id,
    customer_user_id,
):
    try:
        response = HttpResponse(
            toconline_gateway.download_sales_document(document_id),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response
    except Exception:
        logger.exception(
            "Could not download activity TOConline document",
            extra={"entitlement_id": entitlement_id},
        )
        messages.error(request, _("Could not retrieve the PDF document."))
        if request.user.is_staff:
            return redirect("backoffice:user_detail", pk=customer_user_id)
        return redirect("activities:documents")


class SessionListView(CustomerRequiredMixin, ListView):
    template_name = "activities/session_list.html"
    context_object_name = "sessions"
    paginate_by = 20

    def get_queryset(self):
        sessions = list(
            ActivitySession.objects.filter(
                starts_at__gt=timezone.now(),
                is_cancelled=False,
            )
            .select_related("catalog_type")
            .prefetch_related("eligible_levels", "trainers")
            .annotate(
                reserved_places_value=Count(
                    "reservations",
                    filter=Q(reservations__status__in=RESERVED_PLACE_STATUSES),
                )
            )
            .order_by("starts_at", "pk")
        )
        for session in sessions:
            session.closure_message = closure_reason(
                session.catalog_type,
                session.starts_at,
                session.ends_at,
            )
        return sessions


class ReserveSessionView(CustomerRequiredMixin, FormView):
    template_name = "activities/reserve.html"
    form_class = ReserveSessionForm

    def dispatch(self, request, *args, **kwargs):
        self.session = get_object_or_404(
            ActivitySession,
            pk=kwargs["pk"],
            starts_at__gt=timezone.now(),
            is_cancelled=False,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(customer=self.customer, session=self.session)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session"] = self.session
        return context

    def form_valid(self, form):
        entitlement = form.cleaned_data["entitlement"]
        if entitlement.customer_id != self.customer.pk:
            raise Http404
        try:
            reserve_session(
                entitlement=entitlement,
                session=self.session,
                actor=self.request.user,
                notes=form.cleaned_data["notes"],
            )
        except ActivityReservationError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        messages.success(self.request, _("Session booked."))
        return redirect("activities:portal")


class CancelReservationView(CustomerRequiredMixin, View):
    def post(self, request, pk):
        reservation = get_object_or_404(
            ActivityReservation,
            pk=pk,
            entitlement__customer=self.customer,
        )
        try:
            _, credit_returned = cancel_reservation(reservation, actor=request.user)
        except ActivityReservationError as exc:
            messages.error(request, exc.messages[0])
        else:
            message = _("Reservation cancelled and the pack credit was returned.")
            if not credit_returned:
                message = _("Reservation cancelled. The cancellation deadline had passed.")
            messages.success(request, message)
        return redirect("activities:portal")


class CancelSubscriptionView(CustomerRequiredMixin, View):
    def post(self, request, pk):
        entitlement = get_object_or_404(
            ServiceEntitlement.objects.select_related("offering"),
            pk=pk,
            customer=self.customer,
            offering__mode="subscription",
            status__in=(
                EntitlementStatus.ACTIVE,
                EntitlementStatus.PAST_DUE,
                EntitlementStatus.CANCEL_AT_PERIOD_END,
                EntitlementStatus.SUSPENDED,
            ),
        )
        try:
            cancel_subscription(entitlement, actor=request.user)
        except PaymentGatewayError as exc:
            logger.exception(
                "Could not stop activity subscription",
                extra={"entitlement_id": entitlement.pk},
            )
            messages.error(request, str(exc))
        else:
            messages.success(request, _("The subscription will not renew again."))
        return redirect("activities:portal")


class PaymentReturnView(CustomerRequiredMixin, DetailView):
    template_name = "activities/payment_return.html"
    context_object_name = "entitlement"

    def get_queryset(self):
        return ServiceEntitlement.objects.filter(customer=self.customer).select_related(
            "pet",
            "offering__catalog_offer__catalog_item",
        )
