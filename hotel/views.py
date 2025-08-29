import logging
import math

from django.conf import settings
from django.urls import reverse_lazy
from django.http import HttpRequest, Http404, HttpResponse
from django.views.generic import View, TemplateView, DetailView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.core.paginator import Paginator

from stripe import StripeError

from djstripe.models import (
    APIKey as StripeAPIKey,
    Product as StripeProduct,
    Refund as StripeRefund
)

from djstripe.models.checkout import (
    Session as StripeCheckoutSession
)

from olhocao.toconline import get_toconline, TocOnlineResource
from pets.models import Pet
from accounts.models import Account

from . import models, forms


logger = logging.getLogger(__name__)


def get_acting_account(request: HttpRequest) -> Account:
    """Return the account to use in booking flows.

    If a staff user initiated a booking on behalf of someone, the session
    includes 'acting_user_id'. Use that user's account; otherwise, use the
    current user's account.
    """

    # only staff members can book an hotel in behalf of a customer
    if request.user.is_staff and request.session.has_key("acting_user_id"):
        acting_user_id = request.session.get("acting_user_id")
        account, _ = Account.objects.get_or_create(user_id=acting_user_id)
        return account

    return request.user.account


def get_hotel_stays():
    return StripeProduct.objects.filter(
        active=True,
        metadata__family__icontains="hotel",
        metadata__type__icontains="stay",
        stripe_data__default_price__isnull=False
    ).prefetch_related(
        'stripe_data__default_price'
    ).all()


def get_hotel_services():
    return StripeProduct.objects.filter(
        active=True,
        metadata__family__icontains="hotel",
        metadata__type__icontains="services",
        stripe_data__default_price__isnull=False,
    ).prefetch_related(
        'stripe_data__default_price'
    ).all()


def create_checkout_session(
    request: HttpRequest,
    booking: models.Booking,
    *,
    customer_email: str | None = None,
):
    line_items = []

    for stay in booking.stays.all():
        line_items.append({
            'price': stay.stripe_price.id,
            'quantity': stay.duration_days
        })

    for stay in booking.stays.all():
        for service in stay.services.all():
            line_items.append({
                'price': service.stripe_price.id,
                'quantity': service.quantity
            })

    booking_payment_verify_url = reverse_lazy(
        'hotel:booking_payment_verify',
        kwargs={'booking_id': booking.id}
    )

    success_url = (
        request.build_absolute_uri(booking_payment_verify_url)
        + '?session_id={CHECKOUT_SESSION_ID}'
    )

    booking_retry_url = reverse_lazy(
        'hotel:booking_retry', kwargs={'booking_id': booking.id}
    )

    cancel_url = request.build_absolute_uri(
        booking_retry_url
    )

    stripe_api_key = StripeAPIKey.objects.first()

    return StripeCheckoutSession._api_create(
        api_key=stripe_api_key.secret,
        livemode=stripe_api_key.livemode,
        payment_method_types=['card'],
        line_items=line_items,
        mode='payment',
        allow_promotion_codes=True,  # Enable coupon codes
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=(
            customer_email or
            get_acting_account(request).user.email
        ),
        metadata={
            'booking_id': str(booking.id),
            'account_id': str(booking.account.id),
        },
    )


class BookingStayListView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_stay_list.html"
    form_class = forms.BookingStayForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get available stays
        stays = get_hotel_stays()

        # Get user's pets
        pets = Pet.objects.filter(
            owner=get_acting_account(self.request),
            deleted_at__isnull=True
        ).all()

        context.update(
            {
                "stays": stays,
                "pets": pets,
            }
        )

        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["pets"] = Pet.objects.filter(
            owner=get_acting_account(self.request)
        ).all()
        return kwargs

    def form_valid(self, form):
        self.request.session["booking_data"] = {
            "stay_id": form.cleaned_data["stay_id"],
            "pet_ids": form.cleaned_data["pet_ids"],
            "start_date": form.cleaned_data["start_date"].isoformat(),
            "end_date": form.cleaned_data["end_date"].isoformat(),
        }

        return redirect("hotel:booking_services")


class BookingStayServiceListView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_stay_service_list.html"
    form_class = forms.PetServiceForm

    def get_selected_pets(self):
        booking_data = self.request.session.get("booking_data", {})

        if not booking_data:
            raise Http404(
                _("Booking Session Expired")
            )

        return Pet.objects.filter(
            id__in=booking_data["pet_ids"],
            owner=get_acting_account(self.request),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        booking_data = self.request.session.get("booking_data", {})
        if not booking_data:
            raise Http404("Booking session expired")

        start_date = timezone.datetime.fromisoformat(
            booking_data["start_date"]
        ).date()
        end_date = timezone.datetime.fromisoformat(
            booking_data["end_date"]
        ).date()

        context.update(
            {
                "services": get_hotel_services(),
                "pets": self.get_selected_pets(),
                "start_date": start_date,
                "end_date": end_date,
                "duration": (end_date - start_date).days,
            }
        )

        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        # Get booking data from session
        booking_data = self.request.session.get("booking_data", {})
        if not booking_data:
            raise Http404("Booking session expired")

        # Get pets and duration
        pets = self.get_selected_pets()

        start_date = timezone.datetime.fromisoformat(
            booking_data["start_date"]
        ).date()
        end_date = timezone.datetime.fromisoformat(
            booking_data["end_date"]
        ).date()

        duration = (end_date - start_date).days

        # Get available services
        services = get_hotel_services()

        kwargs.update({
            "pets": pets,
            "services": services,
            "duration": duration
        })

        return kwargs

    def form_valid(self, form):
        pets_services = {}

        for field_name, quantity in form.cleaned_data.items():
            if quantity and quantity > 0:
                parts = field_name.split("-")
                pet_id = parts[1]
                service_id = parts[3]

                if pet_id not in pets_services:
                    pets_services[pet_id] = {}

                pets_services[pet_id][service_id] = quantity

        # Update session
        booking_data = self.request.session.get("booking_data", {})
        booking_data["pets_services"] = pets_services
        self.request.session["booking_data"] = booking_data

        return redirect("hotel:booking_review")


class BookingReviewView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_review.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get booking data from session
        booking_data = self.request.session.get("booking_data", {})
        if not booking_data:
            messages.error(
                self.request,
                _("Please start your booking from the beginning"),
            )
            return redirect("hotel:booking_stay")

        # Get stay details
        start_date = timezone.datetime.fromisoformat(
            booking_data["start_date"]
        ).date()
        end_date = timezone.datetime.fromisoformat(
            booking_data["end_date"]
        ).date()
        duration = (end_date - start_date).days

        # Get selected stay
        stay = StripeProduct.objects.get(
            id=booking_data["stay_id"]
        )

        # Get selected pets
        pets = Pet.objects.filter(
            id__in=booking_data["pet_ids"],
            owner=get_acting_account(self.request),
        )

        nr_pets = len(pets)

        # Get selected services
        pets_services_data = booking_data.get("pets_services", {})
        services_map = {s.id: s for s in get_hotel_services()}

        # Calculate pricing
        pricing = {
            "stay": {
                "unit_price": stay.default_price.unit_amount,
                "total_price": (
                    stay.default_price.unit_amount * duration * nr_pets
                ),
            },
            "services": {},
            "grand_total": stay.default_price.unit_amount * duration * nr_pets,
        }

        # Process services
        pets_services = {}

        for pet in pets:
            pet_id = str(pet.id)

            pets_services[pet_id] = {
                "pet": pet,
                "services": []
            }

            if pet_id in pets_services_data:
                for service_id, quantity in pets_services_data[pet_id].items():
                    if quantity > 0:
                        service = services_map[service_id]
                        unit_price = service.default_price.unit_amount
                        total_price = unit_price * quantity

                        pets_services[pet_id]["services"].append(
                            {
                                "service": service,
                                "quantity": quantity,
                                "unit_price": unit_price,
                                "total_price": total_price,
                            }
                        )

                        # Add to pricing summary
                        if service_id not in pricing["services"]:
                            pricing["services"][service_id] = {
                                "service": service,
                                "quantity": 0,
                                "total_price": 0
                            }

                        pricing["services"][service_id]["quantity"] += quantity
                        pricing["services"][service_id][
                            "total_price"
                        ] += total_price
                        pricing["grand_total"] += total_price

        context.update(
            {
                "booking_data": booking_data,
                "stay": stay,
                "pets": pets,
                "pets_services": pets_services,
                "pricing": pricing,
                "start_date": start_date,
                "end_date": end_date,
                "duration": duration,
            }
        )

        return context


class BookingConfirmView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_confirm.html"
    form_class = forms.BookingPaymentForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking_data = self.request.session.get("booking_data", {})

        if not booking_data:
            messages.error(
                self.request,
                _("Please start your booking from the beginning"),
            )
            return redirect("hotel:booking_stay")

        # Calculate total amount
        stay = StripeProduct.objects.get(id=booking_data["stay_id"])
        stay_price = stay.default_price.unit_amount

        duration = (
            timezone.datetime.fromisoformat(booking_data["end_date"]).date()
            - timezone.datetime.fromisoformat(
                booking_data["start_date"]
            ).date()
        ).days

        total = stay_price * duration * len(booking_data["pet_ids"])

        # Add services if any
        if "pets_services" in booking_data:
            for pet_id, services in booking_data["pets_services"].items():
                for service_id, quantity in services.items():
                    service = StripeProduct.objects.get(id=service_id)
                    service_price = service.default_price.unit_amount
                    total += service_price * quantity

        context["total_amount"] = total
        return context

    def create_booking(self, form):
        booking_data = self.request.session["booking_data"]

        # Create booking
        booking = models.Booking.objects.create(
            account=get_acting_account(self.request),
            status="pending",
            notes=form.cleaned_data.get("special_requests", ""),
        )

        # Get stay details
        stay_service = StripeProduct.objects.get(id=booking_data["stay_id"])

        # Create stays for each pet
        for pet_id in booking_data["pet_ids"]:
            pet = Pet.objects.get(
                pk=pet_id,
                owner=get_acting_account(self.request),
            )

            # Create booking stay
            stay = models.BookingStay.objects.create(
                booking=booking,
                pet=pet,
                stripe_product_id=stay_service.id,
                start_date=booking_data["start_date"],
                end_date=booking_data["end_date"],
            )

            # Add services if any
            if str(pet_id) in booking_data.get("pets_services", {}):
                for service_id, quantity in booking_data["pets_services"][
                    str(pet_id)
                ].items():
                    service = StripeProduct.objects.get(id=service_id)

                    models.BookingService.objects.create(
                        stay=stay,
                        pet=pet,
                        stripe_product_id=service.id,
                        quantity=quantity,
                    )

        self.request.session["booking_data"]["id"] = booking.pk
        return booking

    def form_valid(self, form):
        if not form.cleaned_data['terms_accepted']:
            return redirect('hotel:booking_stay')

        booking_data = self.request.session.get('booking_data', {})
        if not booking_data:
            return redirect('hotel:booking_stay')

        # Create the booking record
        booking = self.create_booking(form)

        try:
            is_admin_on_behalf = (
                getattr(self.request.user, 'is_staff', False)
                and self.request.session.get('acting_user_id') is not None
            )

            checkout_session = create_checkout_session(
                self.request,
                booking,
                customer_email=(
                    booking.account.user.email if is_admin_on_behalf else None
                ),
            )

            # Save Stripe session ID to booking
            booking.stripe_checkout_session_id = checkout_session.id
            booking.save()

            if is_admin_on_behalf:
                # Email payment link to the customer and finish
                subject = _("Payment link for your booking #%s") % booking.id
                body = _(
                    "Hello,\n\nA booking has been created for you. "
                    "Please complete payment using the link below:\n%s\n\n"
                    "Thank you."
                ) % checkout_session.url

                try:
                    send_mail(
                        subject,
                        body,
                        settings.DEFAULT_FROM_EMAIL,
                        [booking.account.user.email],
                        fail_silently=True,
                    )
                except Exception:
                    logger.warning("Failed to send payment link email.")

                # Clear acting indicator and return to backoffice user detail
                self.request.session.pop('acting_user_id', None)
                messages.success(
                    self.request,
                    _("Payment link emailed to the customer."),
                )
                return redirect(
                    'backoffice:user_detail', pk=booking.account.user.pk
                )

            return redirect(checkout_session.url)

        except StripeError:
            messages.error(
                self.request,
                _("Payment processing error. Please try again.")
            )

            return redirect('hotel:booking_review')


class BookingPaymentVerifyView(LoginRequiredMixin, View):
    def get(self, request, booking_id):
        where = {
            'id': booking_id
        }

        if not request.user.is_staff:
            where['account'] = request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        session_id = request.GET.get(
            'session_id', booking.stripe_checkout_session_id
        )

        stripe_api_key = StripeAPIKey.objects.first()

        session = StripeCheckoutSession(
            id=session_id
        ).api_retrieve(
            stripe_api_key.secret
        )

        if (
            session.payment_status == 'paid'
            and session.metadata.get('booking_id') == str(booking.pk)
        ):
            if not booking.status or not booking.paid_at:
                booking.status = models.BookingStatus.PAID
                booking.paid_at = timezone.now()
                booking.save()

                sale_document = {
                    'document_type': 'FR',
                    'vat_included_prices': True,
                    'customer_business_name': (
                        booking.account.user.get_full_name()
                    ),
                    'lines': [],
                }

                toconline_customer = booking.account.toconline_customer

                if toconline_customer:
                    tax_registration_number = toconline_customer[
                        'attributes'
                    ].get('tax_registration_number')

                    if tax_registration_number:
                        sale_document['customer_id'] = toconline_customer['id']
                        sale_document['customer_business_name'] = (
                            toconline_customer['attributes']['business_name']
                        )
                        sale_document['customer_tax_registration_number'] = (
                            tax_registration_number
                        )
                        sale_document['external_reference'] = booking.id,

                for stay in booking.stays.all():
                    sale_document['lines'].append({
                        'item_type': 'Service',
                        'description': f"{stay.stay_name} ({stay.pet.name})",
                        'quantity': stay.duration_days,
                        # Convert cents to euros
                        'unit_price': (
                            stay.stripe_product.default_price.unit_amount / 100
                        ),
                    })
                    
                    for service in stay.services.all():
                        sale_document['lines'].append({
                            'item_type': 'Service',
                            'description': (
                                f"{service.service_name} ({stay.pet.name})"
                            ),
                            'quantity': service.quantity,
                            # Convert cents to euros
                            'unit_price': (
                                service.stripe_product.default_price.
                                unit_amount
                                / 100
                            ),
                        })

                toconline_sales_document = get_toconline(request).create(
                    TocOnlineResource.COMERCIAL_SALES_DOCUMENTS,
                    **sale_document
                )

                booking.toconline_sale_document_id = (
                    toconline_sales_document.get('id')
                )
                booking.save()

            return redirect('hotel:booking_success', booking_id=booking.id)

        # If payment not confirmed, allow retry
        return redirect('hotel:booking_retry', booking_id=booking.id)


class BookingRetryView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_retry.html"

    def dispatch(self, request, *args, **kwargs):
        where = {
            'id': kwargs['booking_id']
        }

        if not request.user.is_staff:
            where['account'] = request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        # Check if any stay is in the past
        if any(
            stay.end_date < timezone.now().date()
            for stay in booking.stays.all()
        ):
            messages.error(
                request,
                _(
                    "This booking can no longer be paid as the stay dates "
                    "have passed"
                )
            )

            return redirect('hotel:booking_list')
            
        if booking.status != models.BookingStatus.PENDING:
            messages.error(
                request,
                _("This booking doesn't require payment")
            )

            return redirect('hotel:booking_list')

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        where = {
            'id': kwargs['booking_id'],
            'status': models.BookingStatus.PENDING
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        context['booking'] = booking
        context['payment_url'] = None

        try:
            # Check if existing session is still valid
            if booking.stripe_checkout_session_id:
                stripe_api_key = StripeAPIKey.objects.first()

                session = StripeCheckoutSession(
                    id=booking.stripe_checkout_session_id
                ).api_retrieve(
                    api_key=stripe_api_key.secret
                )

                if session.payment_status == 'paid':
                    # Payment was completed, update booking status
                    booking.status = models.BookingStatus.PAID
                    booking.paid_at = timezone.now()
                    booking.save()
                    return redirect(
                        'hotel:booking_success', booking_id=booking.id
                    )

                if session.status == 'open':
                    # Existing session is still valid
                    context['payment_url'] = session.url
                    context['booking'] = booking

                    return context

            # Create a new Stripe session
            checkout_session = create_checkout_session(self.request, booking)

            # Update booking with new session ID
            booking.stripe_checkout_session_id = checkout_session.id
            booking.save()

            context['payment_url'] = checkout_session.url

        except StripeError:
            pass

        return context


class BookingSuccessView(LoginRequiredMixin, TemplateView):
    template_name = 'hotel/booking_success.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        where = {
            'id': kwargs.get('booking_id'),
            'status': models.BookingStatus.PAID,
            'paid_at__isnull': False
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        context['booking'] = booking

        # Clear session booking data after successful payment
        if 'booking_data' in self.request.session:
            del self.request.session['booking_data']

        return context


class HotelBookingListView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_list.html"
    paginate_by = 9

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now().date()

        # Get filter from query params
        status_filter = self.request.GET.get('status', 'all')

        # Base queryset: staff sees all bookings, users see their own
        if not self.request.user.is_staff:
            bookings = models.Booking.objects.all()
        else:
            bookings = models.Booking.objects.filter(
                account=self.request.user.account
            )

        bookings = bookings.prefetch_related('stays').order_by('-created_at')

        # Apply filters
        if status_filter != 'all':
            if status_filter == 'active':
                bookings = bookings.filter(
                    status=models.BookingStatus.PAID,
                    stay__start_date__lte=now,
                    stay__end_date__gte=now
                ).distinct()
            elif status_filter == 'upcoming':
                bookings = bookings.filter(
                    status=models.BookingStatus.PAID,
                    stay__start_date__gt=now
                ).distinct()
            elif status_filter == 'past':
                bookings = bookings.filter(
                    stay__end_date__lt=now
                ).distinct()
            elif status_filter == 'payment':
                bookings = bookings.filter(
                    status=models.BookingStatus.PENDING
                ).exclude(
                    stay__end_date__lt=now
                ).distinct()

        paginator = Paginator(bookings, self.paginate_by)
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context.update({
            'page_obj': page_obj,
            'is_paginated': page_obj.has_other_pages(),
            'status_filter': status_filter,
            'current_date': now,
        })
        return context


class HotelBookingDetailView(LoginRequiredMixin, DetailView):
    model = models.Booking
    template_name = 'hotel/booking_detail.html'
    context_object_name = 'booking'

    def get_queryset(self):
        qs = super().get_queryset()

        if not self.request.user.is_staff:
            return qs.filter(account__user=self.request.user)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking = self.object

        # Add payment URL if booking is pending
        if booking.status == models.BookingStatus.PENDING:
            try:
                # Check if existing session is still valid
                if booking.stripe_checkout_session_id:
                    stripe_api_key = StripeAPIKey.objects.first()

                    session = StripeCheckoutSession(
                        id=booking.stripe_checkout_session_id
                    ).api_retrieve(
                        stripe_api_key.secret
                    )

                    if session.payment_status == 'paid':
                        # Payment was completed, update booking status
                        booking.status = models.BookingStatus.PAID
                        booking.paid_at = timezone.now()
                        booking.save()

                        return redirect(
                            'hotel:booking_success', booking_id=booking.id
                        )

                    if session.status == 'open':
                        # Existing session is still valid
                        context['payment_url'] = session.url
                        return context

                # Create a new Stripe session if needed
                checkout_session = create_checkout_session(
                    self.request, booking
                )

                # Update booking with new session ID
                booking.stripe_checkout_session_id = checkout_session.id
                booking.save()

                context['payment_url'] = checkout_session.url

            except StripeError:
                messages.error(
                    self.request,
                    _(
                        "Error creating payment session. Please try again "
                        "later."
                    )
                )

        return context


class HotelBookingModifyView(LoginRequiredMixin, FormView):
    template_name = 'hotel/booking_modify.html'
    context_object_name = 'booking'
    form_class = forms.BookingModifyForm

    def get_initial(self):
        where = {
            'id': self.kwargs['booking_id'],
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        return {
            'start_date': booking.earliest_start_date,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        where = {
            'id': self.kwargs['booking_id'],
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        context['booking'] = get_object_or_404(
            models.Booking,
            **where
        )

        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        where = {
            'id': self.kwargs['booking_id'],
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        kwargs['original_start'] = booking.earliest_start_date
        kwargs['original_end'] = booking.latest_end_date
        
        return kwargs

    def form_valid(self, form):
        where = {
            'id': self.kwargs['booking_id'],
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')

        if start_date and end_date:
            booking.stays.update(start_date=start_date, end_date=end_date)

        messages.success(
            self.request,
            _("Booking dates updated successfully."),
        )
        return redirect('hotel:booking_detail', pk=booking.id)


class HotelBookingCancelConfirmView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_cancel_confirm.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        where = {
            'id': self.kwargs["booking_id"],
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        context["booking"] = get_object_or_404(
            models.Booking,
            **where
        )

        # Add refund percentage info for the template
        context["refund_notice"] = _(
            "Only {refund_percentage}%% of the payment "
            "will be refunded due to processing costs.",
        ).format(
            refund_percentage=settings.HOTEL_REFUND_PERCENTAGE * 100
        )
        return context


class HotelBookingCancelView(LoginRequiredMixin, View):
    def post(self, request, booking_id):
        where = {
            'id': booking_id
        }

        if not self.request.user.is_staff:
            where['account'] = self.request.user.account

        booking = get_object_or_404(
            models.Booking,
            **where
        )

        if booking.status == models.BookingStatus.CANCELLED:
            messages.error(
                request,
                _("This booking has already been cancelled.")
            )
            return redirect('hotel:booking_detail', pk=booking.id)

        if not booking.can_cancel:
            messages.error(
                request,
                _(
                    "You can only cancel up to 7 days before check-in and "
                    "within 6 months of the original booking."
                )
            )
            return redirect('hotel:booking_detail', pk=booking.id)

        toconline = get_toconline(request)

        sale_document = toconline.get(
            TocOnlineResource.COMERCIAL_SALES_DOCUMENTS,
            booking.toconline_sale_document_id
        )

        amending_document = {
            'document_type': 'NC',
            'parent_document_reference': sale_document.get('document_no', ''),
            'lines': [],
            'customer_business_name': booking.account.user.get_full_name(),
            'customer_id': (
                booking.account.toconline_customer.id
                if booking.account.toconline_customer
                else None
            ),
            'customer_tax_registration_number': (
                booking.account.toconline_customer.get(
                    'tax_registration_number', None
                )
                if booking.account.toconline_customer
                else None
            ),
            'vat_included_prices': True,
            'notes': str(
                _(
                    "Booking cancelled and refunded "
                    "({refund_percentage}%% refund applied)"
                ).format(
                    refund_percentage=settings.HOTEL_REFUND_PERCENTAGE * 100
                )
            ),
        }

        for stay in booking.stays.all():
            amending_document['lines'].append({
                'item_type': 'Service',
                'description': f"{stay.stay_name} ({stay.pet.name})",
                'quantity': stay.duration_days,
                'unit_price': math.floor(
                    stay.stripe_product.default_price.unit_amount
                    * settings.HOTEL_REFUND_PERCENTAGE
                ) / 100
            })

            for service in stay.services.all():
                amending_document['lines'].append({
                    'item_type': 'Service',
                    'description': f"{service.service_name} ({stay.pet.name})",
                    'quantity': service.quantity,
                    'unit_price': math.floor(
                        service.stripe_product.default_price.unit_amount
                        * settings.HOTEL_REFUND_PERCENTAGE
                    ) / 100,
                })

        toconline_amend_document = toconline.create(
            TocOnlineResource.COMERCIAL_SALES_DOCUMENTS,
            **amending_document
        )

        booking.toconline_amend_document_id = toconline_amend_document.get(
            'id'
        )
        booking.status = models.BookingStatus.CANCELLED
        booking.save()

        try:
            session = StripeCheckoutSession.objects.get(
                id=booking.stripe_checkout_session_id
            )

            charge = session.payment_intent.charges.first()

            if not charge:
                raise ValueError("No charge found for this payment.")

            # Calculate refund amount
            amount_refund = math.floor(
                charge.amount * settings.HOTEL_REFUND_PERCENTAGE
            )

            stripe_api_key = StripeAPIKey.objects.first()

            StripeRefund._api_create(
                api_key=stripe_api_key.secret,
                livemode=stripe_api_key.livemode,
                charge=charge.id,
                amount=amount_refund
            )
        except (StripeError, ValueError):
            messages.warning(
                request,
                _(
                    "Refund failed or already processed. Please contact "
                    "support."
                ),
            )
            return redirect('hotel:booking_detail', pk=booking.id)

        messages.success(
            request,
            _(
                "Booking cancelled and, if paid, "
                "{refund_percentage}%% refunded."
            ).format(
                refund_percentage=settings.HOTEL_REFUND_PERCENTAGE * 100
            )
        )
        return redirect('hotel:booking_detail', pk=booking.id)


def download_sales_document_pdf(request, booking_id):
    """
    View to redirect to the PDF URL for the sales document for a booking.
    """

    where = {
        'id': booking_id,
    }

    if not request.user.is_staff:
        where['account'] = request.user.account

    booking = get_object_or_404(
        models.Booking,
        **where
    )

    sales_document_id = booking.toconline_sale_document_id

    if not sales_document_id:
        messages.error(
            request,
            _("No sales document available for this booking."),
        )
        return redirect('hotel:booking_detail', pk=booking.id)

    try:
        toconline = get_toconline(request)

        return HttpResponse(
            toconline.get_sales_document_pdf(sales_document_id),
            content_type='application/pdf'
        )
    except Exception as e:
        logger.error(
            f"Error fetching PDF for booking {booking_id}: {e}"
        )
        messages.error(
            request,
            _(
                "Could not retrieve PDF document. Please try again later."
            ),
        )
        return redirect('hotel:booking_detail', pk=booking.id)
