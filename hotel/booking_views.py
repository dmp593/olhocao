import base64
import binascii
import hmac
import json
import logging
from collections.abc import Mapping

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, FormView, TemplateView, View

from accounts.models import CustomerProfile
from discounts.services import PromotionUnavailable
from olhocao.integrations.toconline import toconline_gateway
from pets.models import Pet

from . import forms, models
from .services.bookings import (
    BookingValidationError,
    available_extras,
    available_stays,
    build_booking_review,
    cancel_booking,
    create_booking,
    reschedule_booking,
)
from .services.payments import (
    PaymentGatewayError,
    PaymentGatewayUnavailable,
    create_payment_attempt,
    process_payment_notification,
    refresh_payment_attempt,
    send_booking_payment_link_email,
)
from .services.pricing import (
    BookingPriceChangeError,
    preview_booking_promotion,
    set_booking_final_price,
)
from .services.refunds import (
    refundable_booking_balance,
    request_booking_refund,
    retry_booking_refund_request,
)

logger = logging.getLogger(__name__)
MAX_EASYPAY_WEBHOOK_BYTES = 64 * 1024


def get_booking_customer(request):
    if request.user.is_staff:
        customer_id = request.session.get("assisted_customer_id")
        if not customer_id:
            return None
        customer = (
            CustomerProfile.objects.select_related("user")
            .filter(pk=customer_id, user__is_active=True, user__is_staff=False)
            .first()
        )
        if customer is None:
            request.session.pop("assisted_customer_id", None)
        return customer
    return getattr(request.user, "customer_profile", None)


def get_owned_booking(request, **lookup):
    if not request.user.is_staff:
        customer = get_booking_customer(request)
        if customer is None:
            raise Http404(_("Customer profile not found."))
        lookup["customer"] = customer
    return get_object_or_404(models.Booking, **lookup)


class BookingStayListView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_stay_list.html"
    form_class = forms.BookingStayForm

    def dispatch(self, request, *args, **kwargs):
        if get_booking_customer(request) is None:
            if request.user.is_staff:
                messages.info(request, _("Select a customer before starting an assisted booking."))
                return redirect("backoffice:users_list")
            messages.error(
                request,
                _("Your customer profile is incomplete. Please contact support."),
            )
            return redirect("frontoffice:home")
        return super().dispatch(request, *args, **kwargs)

    def get_pets(self):
        return Pet.objects.filter(
            owner=get_booking_customer(self.request),
            deleted_at__isnull=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "stays": available_stays(),
                "pets": self.get_pets(),
                "today": timezone.localdate(),
            }
        )
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["pets"] = self.get_pets()
        return kwargs

    def form_valid(self, form):
        self.request.session["booking_data"] = {
            "offer_id": form.cleaned_data["offer_id"],
            "pet_ids": form.cleaned_data["pet_ids"],
            "start_date": form.cleaned_data["start_date"].isoformat(),
            "end_date": form.cleaned_data["end_date"].isoformat(),
        }
        return redirect("hotel:booking_services")


class BookingStayServiceListView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_stay_service_list.html"
    form_class = forms.PetServiceForm

    def get_review(self):
        booking_data = self.request.session.get("booking_data")
        if not booking_data:
            raise Http404(_("Booking Session Expired"))
        try:
            return build_booking_review(
                get_booking_customer(self.request),
                booking_data,
            )
        except BookingValidationError as exc:
            raise Http404(str(exc)) from exc

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        review = self.get_review()
        context.update(
            {
                "services": available_extras(review.stay),
                "pets": review.pets,
                "start_date": review.start_date,
                "end_date": review.end_date,
                "duration": review.duration,
            }
        )
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        review = self.get_review()
        kwargs.update(
            {
                "pets": review.pets,
                "services": available_extras(review.stay),
                "duration": review.duration,
                "start_date": review.start_date,
                "end_date": review.end_date,
            }
        )
        return kwargs

    def form_valid(self, form):
        booking_data = self.request.session["booking_data"]
        booking_data["pets_services"] = form.selected_services()
        self.request.session["booking_data"] = booking_data
        return redirect("hotel:booking_review")


class BookingReviewView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_review.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking_data = self.request.session.get("booking_data")
        if not booking_data:
            raise Http404(_("Booking Session Expired"))

        try:
            review = build_booking_review(
                get_booking_customer(self.request),
                booking_data,
            )
        except BookingValidationError as exc:
            raise Http404(str(exc)) from exc

        promotion_code = self.request.GET.get("promotion_code", "").strip()
        promotion_quote = None
        promotion_error = ""
        if promotion_code:
            try:
                promotion_quote = preview_booking_promotion(
                    review,
                    customer=get_booking_customer(self.request),
                    code=promotion_code,
                )
                promotion_code = promotion_quote.promotion.code
            except PromotionUnavailable as exc:
                promotion_error = str(exc)

        context.update(
            {
                "booking_data": booking_data,
                "stay": review.stay,
                "pets": review.pets,
                "pets_services": review.pets_services,
                "pricing": review.pricing,
                "start_date": review.start_date,
                "end_date": review.end_date,
                "duration": review.duration,
                "is_assisted_booking": (
                    self.request.user.is_staff
                    and self.request.session.get("assisted_customer_id") is not None
                ),
                "promotion_form": forms.PromotionCodeForm(
                    initial={"promotion_code": promotion_code}
                ),
                "promotion_code": promotion_code if promotion_quote else "",
                "promotion_quote": promotion_quote,
                "promotion_error": promotion_error,
            }
        )
        return context


class BookingConfirmView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_confirm.html"
    form_class = forms.BookingPaymentForm

    def form_valid(self, form):
        booking_data = self.request.session.get("booking_data")
        if not booking_data:
            raise Http404(_("Booking Session Expired"))

        customer = get_booking_customer(self.request)
        if customer is None:
            messages.error(self.request, _("The assisted customer is no longer available."))
            return redirect("backoffice:users_list")
        booking = None
        existing_booking_id = booking_data.get("id")
        if existing_booking_id:
            booking = models.Booking.objects.filter(
                pk=existing_booking_id,
                customer=customer,
                status=models.BookingStatus.PENDING,
            ).first()

        try:
            if booking is None:
                booking = create_booking(
                    customer,
                    booking_data,
                    notes=form.cleaned_data.get("special_requests", ""),
                    actor=self.request.user,
                    promotion_code=form.cleaned_data.get("promotion_code", ""),
                )
                booking_data["id"] = booking.pk
                self.request.session["booking_data"] = booking_data
            is_assisted_booking = (
                self.request.user.is_staff
                and self.request.session.get("assisted_customer_id") is not None
            )
            if is_assisted_booking:
                self.request.session.pop("assisted_customer_id", None)
                self.request.session.pop("booking_data", None)
                if booking.status == models.BookingStatus.COMPLIMENTARY:
                    messages.success(
                        self.request,
                        _("The complimentary booking was created and confirmed."),
                    )
                else:
                    messages.success(
                        self.request,
                        _(
                            "The booking was created. Review its final price, then send "
                            "the payment link to the customer."
                        ),
                    )
                return redirect("hotel:booking_detail", pk=booking.pk)

            if booking.status == models.BookingStatus.COMPLIMENTARY:
                self.request.session.pop("booking_data", None)
                return redirect("hotel:booking_success", booking_id=booking.pk)
            payment = create_payment_attempt(
                booking,
                notify_customer=True,
            )
        except (BookingValidationError, PromotionUnavailable) as exc:
            messages.error(self.request, str(exc))
            return redirect("hotel:booking_review")
        except PaymentGatewayError as exc:
            messages.error(self.request, str(exc))
            if booking is not None:
                return redirect("hotel:booking_retry", booking_id=booking.pk)
            return redirect("hotel:booking_review")

        return redirect(payment.payment_url)


class BookingPaymentVerifyView(LoginRequiredMixin, View):
    def get(self, request, booking_id):
        booking = get_owned_booking(request, id=booking_id)
        payment = booking.payment_attempts.first()
        if payment:
            try:
                payment = refresh_payment_attempt(payment)
            except PaymentGatewayError:
                logger.exception(
                    "Could not refresh Easypay payment",
                    extra={"booking_id": booking.pk},
                )
        if payment and payment.status == models.PaymentStatus.PAID:
            return redirect("hotel:booking_success", booking_id=booking.pk)
        return redirect("hotel:booking_retry", booking_id=booking.pk)


@method_decorator(csrf_exempt, name="dispatch")
class EasypayWebhookView(View):
    def post(self, request):
        if not valid_easypay_webhook_authorization(request):
            return HttpResponse(status=401)

        content_length = request.META.get("CONTENT_LENGTH")
        try:
            declared_size = int(content_length) if content_length else 0
        except (TypeError, ValueError):
            return JsonResponse({"error": "invalid content length"}, status=400)
        if declared_size > MAX_EASYPAY_WEBHOOK_BYTES:
            return JsonResponse({"error": "payload too large"}, status=413)

        try:
            body = request.body
            if len(body) > MAX_EASYPAY_WEBHOOK_BYTES:
                return JsonResponse({"error": "payload too large"}, status=413)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, Mapping):
                return JsonResponse({"error": "JSON object required"}, status=400)
            if str(payload.get("key") or "").startswith("activity-"):
                from activities.services.billing import process_activity_notification

                process_activity_notification(payload)
            else:
                process_payment_notification(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JsonResponse({"error": "invalid JSON"}, status=400)
        except PaymentGatewayUnavailable:
            logger.exception("Easypay webhook verification is temporarily unavailable")
            return JsonResponse({"error": "temporarily unavailable"}, status=503)
        except PaymentGatewayError as exc:
            logger.warning("Rejected Easypay webhook: %s", exc)
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception:
            logger.exception("Unexpected Easypay webhook failure")
            return JsonResponse({"error": "processing failed"}, status=500)
        return HttpResponse("OK", content_type="text/plain")


def valid_easypay_webhook_authorization(request):
    expected_username = settings.EASYPAY_WEBHOOK_USERNAME
    expected_password = settings.EASYPAY_WEBHOOK_PASSWORD
    if not expected_username or not expected_password:
        return False

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(
        password,
        expected_password,
    )


class BookingRetryView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_retry.html"

    def dispatch(self, request, *args, **kwargs):
        self.booking = get_owned_booking(request, id=kwargs["booking_id"])
        if (
            self.booking.earliest_start_date is not None
            and self.booking.earliest_start_date < timezone.localdate()
        ):
            messages.error(
                request,
                _("This booking can no longer be paid because its start date passed."),
            )
            return redirect("hotel:booking_list")
        if self.booking.status != models.BookingStatus.PENDING:
            return redirect("hotel:booking_detail", pk=self.booking.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment = self.booking.payment_attempts.first()
        if payment:
            try:
                payment = refresh_payment_attempt(payment)
            except PaymentGatewayError:
                logger.exception(
                    "Could not refresh Easypay payment",
                    extra={"booking_id": self.booking.pk},
                )
        if payment and payment.status == models.PaymentStatus.PAID:
            return redirect("hotel:booking_success", booking_id=self.booking.pk)

        context["booking"] = self.booking
        context["is_staff_payment_delivery"] = self.request.user.is_staff
        context["payment_url"] = (
            payment.payment_url
            if payment and payment.can_be_paid and not self.request.user.is_staff
            else ""
        )
        context["payment_retry_url"] = reverse(
            "hotel:booking_retry",
            kwargs={"booking_id": self.booking.pk},
        )
        context["support_email"] = settings.SUPPORT_EMAIL
        return context

    def post(self, request, *args, **kwargs):
        try:
            payment = create_payment_attempt(
                self.booking,
                notify_customer=not request.user.is_staff,
            )
            if request.user.is_staff:
                send_booking_payment_link_email(payment)
        except PaymentGatewayError as exc:
            messages.error(request, str(exc))
            return redirect("hotel:booking_retry", booking_id=self.booking.pk)
        if request.user.is_staff:
            messages.success(request, _("The payment link was sent to the customer by email."))
            return redirect("hotel:booking_detail", pk=self.booking.pk)
        return redirect(payment.payment_url)


class BookingSuccessView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_success.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking = get_owned_booking(
            self.request,
            id=kwargs["booking_id"],
            status__in=(
                models.BookingStatus.PAID,
                models.BookingStatus.COMPLIMENTARY,
            ),
        )
        context["booking"] = booking
        self.request.session.pop("booking_data", None)
        return context


class HotelBookingListView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_list.html"
    paginate_by = 9

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            return redirect("backoffice:dashboard")
        if request.user.is_authenticated and get_booking_customer(request) is None:
            messages.info(request, _("Complete your account details to continue."))
            return redirect("accounts:user_change")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        status_filter = self.request.GET.get("status", "all")
        bookings = models.Booking.objects.prefetch_related("stays")
        if not self.request.user.is_staff:
            bookings = bookings.filter(customer=get_booking_customer(self.request))

        if status_filter == "active":
            bookings = bookings.filter(
                status__in=models.CONFIRMED_BOOKING_STATUSES,
                stay__start_date__lte=today,
                stay__end_date__gte=today,
            ).distinct()
        elif status_filter == "upcoming":
            bookings = bookings.filter(
                status__in=models.CONFIRMED_BOOKING_STATUSES,
                stay__start_date__gt=today,
            ).distinct()
        elif status_filter == "past":
            bookings = bookings.filter(stay__end_date__lt=today).distinct()
        elif status_filter == "pending":
            bookings = bookings.filter(
                status=models.BookingStatus.PENDING,
                stay__end_date__gte=today,
            ).distinct()
        elif status_filter == "disputed":
            bookings = bookings.filter(status=models.BookingStatus.PAYMENT_DISPUTED)

        paginator = Paginator(bookings.order_by("-created_at"), self.paginate_by)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        context.update(
            {
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
                "status_filter": status_filter,
                "current_date": today,
            }
        )
        return context


class HotelBookingDetailView(LoginRequiredMixin, DetailView):
    model = models.Booking
    template_name = "hotel/booking_detail.html"
    context_object_name = "booking"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .prefetch_related(
                "stays__services",
                "stays__pet__species",
                "stays__media",
                "payment_attempts",
                "payment_attempts__booking_refunds__requested_by",
            )
        )
        if not self.request.user.is_staff:
            customer = get_booking_customer(self.request)
            queryset = queryset.filter(customer=customer) if customer else queryset.none()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment = (
            self.object.payment_attempts.filter(
                status=models.PaymentStatus.PAID,
                provider_capture_id__gt="",
            ).first()
            or self.object.payment_attempts.first()
        )
        context["payment"] = payment
        booking_refunds = (
            payment.booking_refunds.select_related("requested_by").all() if payment else ()
        )
        context["booking_refunds"] = booking_refunds
        context["latest_booking_refund"] = booking_refunds.first() if payment else None
        refundable_amount = (
            refundable_booking_balance(payment)
            if payment and payment.status == models.PaymentStatus.PAID
            else 0
        )
        context["refundable_amount"] = refundable_amount
        context["staff_can_request_refund"] = (
            self.request.user.is_staff
            and payment is not None
            and payment.status == models.PaymentStatus.PAID
            and refundable_amount > 0
            and self.object.status != models.BookingStatus.PAYMENT_DISPUTED
        )
        context["payment_url"] = (
            payment.payment_url
            if (
                self.object.status == models.BookingStatus.PENDING
                and payment
                and payment.can_be_paid
                and not self.request.user.is_staff
            )
            else None
        )
        context["payment_retry_url"] = reverse(
            "hotel:booking_retry",
            kwargs={"booking_id": self.object.pk},
        )
        context["staff_can_manage_booking"] = (
            self.request.user.is_staff
            and self.object.status
            not in {
                models.BookingStatus.CANCELLED,
                models.BookingStatus.PAYMENT_DISPUTED,
            }
        )
        context["staff_can_adjust_price"] = (
            self.request.user.is_staff
            and self.object.status
            in {
                models.BookingStatus.PENDING,
                models.BookingStatus.COMPLIMENTARY,
            }
            and not self.object.toconline_sale_document_id
            and not self.object.payment_attempts.filter(
                status__in=(
                    models.PaymentStatus.PAID,
                    models.PaymentStatus.REFUND_PENDING,
                    models.PaymentStatus.REFUND_FAILED,
                    models.PaymentStatus.REFUNDED,
                    models.PaymentStatus.DISPUTED,
                )
            ).exists()
        )
        return context


class BookingRefundView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_refund.html"
    form_class = forms.BookingRefundForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404
        self.booking = get_object_or_404(
            models.Booking.objects.select_related("customer__user"),
            pk=kwargs["booking_id"],
        )
        if self.booking.status == models.BookingStatus.PAYMENT_DISPUTED:
            messages.error(request, _("A disputed payment requires financial review."))
            return redirect("hotel:booking_detail", pk=self.booking.pk)
        self.payment = (
            self.booking.payment_attempts.filter(
                status=models.PaymentStatus.PAID,
                provider_capture_id__gt="",
            )
            .order_by("paid_at", "pk")
            .first()
        )
        if self.payment is None:
            messages.error(request, _("This booking has no confirmed payment to refund."))
            return redirect("hotel:booking_detail", pk=self.booking.pk)
        self.refundable_amount = refundable_booking_balance(self.payment)
        if self.refundable_amount <= 0:
            messages.info(request, _("This payment has already been fully refunded."))
            return redirect("hotel:booking_detail", pk=self.booking.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["refundable_amount"] = self.refundable_amount
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            booking=self.booking,
            payment=self.payment,
            refundable_amount=self.refundable_amount,
            already_refunded_amount=(self.payment.amount_eur - self.refundable_amount),
        )
        return context

    def form_valid(self, form):
        try:
            request_booking_refund(
                self.payment,
                amount=form.cleaned_data["amount_eur"],
                actor=self.request.user,
                reason=form.cleaned_data["reason"],
            )
        except PaymentGatewayError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("The refund was requested and is awaiting provider confirmation."),
        )
        return redirect("hotel:booking_detail", pk=self.booking.pk)


class BookingRefundRetryView(LoginRequiredMixin, View):
    def post(self, request, booking_id, refund_id):
        if not request.user.is_staff:
            raise Http404
        refund = get_object_or_404(
            models.BookingRefund.objects.select_related("payment_attempt__booking"),
            pk=refund_id,
            payment_attempt__booking_id=booking_id,
            status=models.BookingRefundStatus.FAILED,
        )
        try:
            retry_booking_refund_request(refund, actor=request.user)
        except PaymentGatewayError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("The refund request was submitted again."))
        return redirect("hotel:booking_detail", pk=booking_id)


class BookingPriceAdjustmentView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_price_adjustment.html"
    form_class = forms.BookingPriceAdjustmentForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404
        self.booking = get_object_or_404(
            models.Booking.objects.select_related("customer__user").prefetch_related(
                "stays__services",
                "price_adjustments__actor",
                "payment_attempts",
            ),
            pk=kwargs["booking_id"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["booking"] = self.booking
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["booking"] = self.booking
        context["adjustments"] = self.booking.price_adjustments.select_related("actor")
        return context

    def form_valid(self, form):
        try:
            booking = set_booking_final_price(
                self.booking,
                final_price_eur=form.cleaned_data["final_price_eur"],
                reason=form.cleaned_data["reason"],
                actor=self.request.user,
            )
        except (BookingPriceChangeError, PaymentGatewayError) as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        if booking.status == models.BookingStatus.COMPLIMENTARY:
            messages.success(self.request, _("The booking is confirmed as complimentary."))
        else:
            messages.success(
                self.request,
                _("The final price was updated. A new payment link can now be sent."),
            )
        return redirect("hotel:booking_detail", pk=booking.pk)


class HotelBookingModifyView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_modify.html"
    form_class = forms.BookingModifyForm

    def get_booking(self):
        if not hasattr(self, "booking"):
            self.booking = get_owned_booking(self.request, id=self.kwargs["booking_id"])
        return self.booking

    def dispatch(self, request, *args, **kwargs):
        booking = self.get_booking()
        staff_can_modify = request.user.is_staff and booking.status not in {
            models.BookingStatus.CANCELLED,
            models.BookingStatus.PAYMENT_DISPUTED,
        }
        if not booking.can_modify and not staff_can_modify:
            messages.error(request, _("This booking can no longer be changed."))
            return redirect("hotel:booking_detail", pk=booking.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"start_date": self.get_booking().earliest_start_date}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["booking"] = self.get_booking()
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        booking = self.get_booking()
        kwargs.update(
            {
                "original_start": booking.earliest_start_date,
                "original_end": booking.latest_end_date,
                "enforce_customer_policy": not self.request.user.is_staff,
            }
        )
        return kwargs

    def form_valid(self, form):
        booking = self.get_booking()
        start_date = form.cleaned_data.get("start_date")
        end_date = form.cleaned_data.get("end_date")
        if start_date and end_date:
            try:
                reschedule_booking(
                    booking,
                    start_date=start_date,
                    end_date=end_date,
                    actor=self.request.user,
                )
            except BookingValidationError as exc:
                form.add_error(None, str(exc))
                return self.form_invalid(form)
        messages.success(self.request, _("Booking dates updated successfully."))
        return redirect("hotel:booking_detail", pk=booking.pk)


class HotelBookingCancelConfirmView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_cancel_confirm.html"

    def get_booking(self):
        if not hasattr(self, "booking"):
            self.booking = get_owned_booking(self.request, id=self.kwargs["booking_id"])
        return self.booking

    def dispatch(self, request, *args, **kwargs):
        booking = self.get_booking()
        staff_can_cancel = request.user.is_staff and booking.status not in {
            models.BookingStatus.CANCELLED,
            models.BookingStatus.PAYMENT_DISPUTED,
        }
        if not booking.can_cancel and not staff_can_cancel:
            messages.error(request, _("This booking can no longer be cancelled."))
            return redirect("hotel:booking_detail", pk=booking.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["booking"] = self.get_booking()
        context["has_paid_payment"] = (
            context["booking"].payment_attempts.filter(status=models.PaymentStatus.PAID).exists()
        )
        context["refund_notice"] = _(
            "The refund is {refund_percentage}%% of the captured payment."
        ).format(refund_percentage=settings.HOTEL_REFUND_PERCENTAGE * 100)
        return context


class HotelBookingCancelView(LoginRequiredMixin, View):
    def post(self, request, booking_id):
        booking = get_owned_booking(request, id=booking_id)
        if booking.status == models.BookingStatus.CANCELLED:
            messages.error(request, _("This booking has already been cancelled."))
            return redirect("hotel:booking_detail", pk=booking.pk)

        try:
            result = cancel_booking(
                booking,
                actor=request.user,
                enforce_customer_policy=not request.user.is_staff,
            )
        except (BookingValidationError, PaymentGatewayError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("hotel:booking_detail", pk=booking.pk)

        if result.refund_error:
            messages.warning(
                request,
                _(
                    "The booking is cancelled, but part of the financial "
                    "processing requires administrator attention."
                ),
            )
        else:
            messages.success(
                request,
                _("Booking cancelled; any applicable refund has been requested."),
            )
        return redirect("hotel:booking_detail", pk=booking.pk)


@login_required
def download_sales_document_pdf(request, booking_id):
    booking = get_owned_booking(request, id=booking_id)
    if not booking.toconline_sale_document_id:
        messages.error(request, _("No sales document is available."))
        return redirect("hotel:booking_detail", pk=booking.pk)
    try:
        return HttpResponse(
            toconline_gateway.download_sales_document(booking.toconline_sale_document_id),
            content_type="application/pdf",
        )
    except Exception:
        logger.exception(
            "Could not download TOConline document",
            extra={"booking_id": booking.pk},
        )
        messages.error(request, _("Could not retrieve the PDF document."))
        return redirect("hotel:booking_detail", pk=booking.pk)


@login_required
def download_booking_refund_pdf(request, booking_id, refund_id):
    booking = get_owned_booking(request, id=booking_id)
    refund = get_object_or_404(
        models.BookingRefund,
        pk=refund_id,
        payment_attempt__booking=booking,
        status=models.BookingRefundStatus.REFUNDED,
    )
    if not refund.toconline_amend_document_id:
        messages.error(request, _("No refund document is available yet."))
        return redirect("hotel:booking_detail", pk=booking.pk)
    try:
        response = HttpResponse(
            toconline_gateway.download_sales_document(refund.toconline_amend_document_id),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = (
            f'inline; filename="olhocao-booking-refund-{refund.pk}.pdf"'
        )
        return response
    except Exception:
        logger.exception(
            "Could not download TOConline booking refund document",
            extra={"booking_id": booking.pk, "booking_refund_id": refund.pk},
        )
        messages.error(request, _("Could not retrieve the PDF document."))
        return redirect("hotel:booking_detail", pk=booking.pk)
