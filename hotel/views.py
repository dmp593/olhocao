import logging
from django.urls import reverse_lazy
import stripe

from django.http import Http404
from django.views.generic import View, TemplateView, DetailView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.contrib import messages
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _
from django.core.paginator import Paginator

from olhocao.toconline import toconline, TocOnlineResource
from pets.models import Pet

from django import forms
from decimal import Decimal

from . import models, forms


logger = logging.getLogger(__name__)


class BookingStayListView(LoginRequiredMixin, FormView):
    template_name = "hotel/booking_stay_list.html"
    form_class = forms.BookingStayForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get available stays
        family = toconline.list(TocOnlineResource.ITEM_FAMILY, name="Hotel")[0]
        stays = toconline.list(
            TocOnlineResource.SERVICES,
            item_family_id=family["id"],
            service_group=models.ServiceGroup.GENERAL_SERVICE,
            is_active=True,
        )

        # Format stays data
        stays = [
            {
                "id": stay["id"],
                **stay["attributes"],
                **stay["relationships"],
            }
            for stay in stays
        ]

        # Get user's pets
        pets = Pet.objects.filter(owner=self.request.user.account)

        context.update(
            {
                "family": family,
                "stays": stays,
                "pets": pets,
            }
        )

        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["pets"] = Pet.objects.filter(owner=self.request.user.account)
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
            raise Http404("Booking session expired")

        return Pet.objects.filter(
            id__in=booking_data["pet_ids"], owner=self.request.user.account
        )

    def get_available_services(self):
        family = toconline.list(TocOnlineResource.ITEM_FAMILY, name="Hotel")[0]
        services = toconline.list(
            TocOnlineResource.SERVICES,
            item_family_id=family["id"],
            service_group=models.ServiceGroup.EXTRA_SERVICE,
            is_active=True,
        )

        return [
            {
                "id": service["id"],
                "service_type": models.ServiceType.FIXED,
                **service["attributes"],
                **service["relationships"],
            }
            for service in services
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        booking_data = self.request.session.get("booking_data", {})
        if not booking_data:
            raise Http404("Booking session expired")

        start_date = timezone.datetime.fromisoformat(booking_data["start_date"]).date()
        end_date = timezone.datetime.fromisoformat(booking_data["end_date"]).date()

        context.update(
            {
                "services": self.get_available_services(),
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

        start_date = timezone.datetime.fromisoformat(booking_data["start_date"]).date()
        end_date = timezone.datetime.fromisoformat(booking_data["end_date"]).date()

        duration = (end_date - start_date).days

        # Get available services
        services = self.get_available_services()

        kwargs.update({"pets": pets, "services": services, "duration": duration})
        return kwargs

    def form_valid(self, form):
        # Process the form data
        services_data = {}
        for field_name, quantity in form.cleaned_data.items():
            if quantity and quantity > 0:
                parts = field_name.split("_")
                pet_id = parts[1]
                service_id = parts[3]

                if pet_id not in services_data:
                    services_data[pet_id] = {}
                services_data[pet_id][service_id] = quantity

        # Update session
        booking_data = self.request.session.get("booking_data", {})
        booking_data["selected_services"] = services_data
        self.request.session["booking_data"] = booking_data

        return redirect("hotel:booking_review")


class BookingReviewView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_review.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get booking data from session
        booking_data = self.request.session.get("booking_data", {})
        if not booking_data:
            messages.error(self.request, "Please start your booking from the beginning")
            return redirect("hotel:booking_stay")

        # Get stay details
        start_date = timezone.datetime.fromisoformat(booking_data["start_date"]).date()
        end_date = timezone.datetime.fromisoformat(booking_data["end_date"]).date()
        duration = (end_date - start_date).days

        # Get selected stay
        family = toconline.list(TocOnlineResource.ITEM_FAMILY, name="Hotel")[0]

        stay = toconline.get(TocOnlineResource.SERVICES, pk=booking_data["stay_id"])

        stay = {
            "id": stay["id"],
            **stay["attributes"],
            **stay["relationships"],
        }

        # Get selected pets
        pets = Pet.objects.filter(
            id__in=booking_data["pet_ids"], owner=self.request.user.account
        )

        # Get selected services
        services_data = booking_data.get("selected_services", {})
        all_services = toconline.list(
            TocOnlineResource.SERVICES, item_family_id=family["id"], is_active=True
        )
        services_map = {
            s["id"]: {
                "id": s["id"],
                "service_type": models.ServiceType.FIXED,
                **s["attributes"],
                **s["relationships"],
            }
            for s in all_services
        }

        # Calculate pricing
        pricing = {
            "stay": {
                "unit_price": Decimal(stay["sales_price"]),
                "total": Decimal(stay["sales_price"]) * duration * len(pets),
            },
            "services": {},
            "grand_total": Decimal(stay["sales_price"]) * duration * len(pets),
        }

        # Process services
        pet_services = {}
        for pet in pets:
            pet_services[str(pet.id)] = {"pet": pet, "services": []}

            if str(pet.id) in services_data:
                for service_id, quantity in services_data[str(pet.id)].items():
                    if service_id in services_map and quantity > 0:
                        service = services_map[service_id]
                        price = Decimal(service["sales_price"])

                        if service["service_type"] == "daily":
                            total = price * duration * quantity
                        else:
                            total = price * quantity

                        pet_services[str(pet.id)]["services"].append(
                            {
                                "service": service,
                                "quantity": quantity,
                                "price": price,
                                "total": total,
                            }
                        )

                        # Add to pricing summary
                        if service_id not in pricing["services"]:
                            pricing["services"][service_id] = {
                                "service": service,
                                "quantity": 0,
                                "total": Decimal(0),
                            }

                        pricing["services"][service_id]["quantity"] += quantity
                        pricing["services"][service_id]["total"] += total
                        pricing["grand_total"] += total

        context.update(
            {
                "booking_data": booking_data,
                "stay": stay,
                "pets": pets,
                "pet_services": pet_services,
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
            messages.error(self.request, "Please start your booking from the beginning")
            return redirect("hotel:booking_stay")

        # Calculate total amount (you might want to move this to a separate method)
        stay = toconline.get(TocOnlineResource.SERVICES, pk=booking_data["stay_id"])
        stay_price = Decimal(stay["attributes"]["sales_price"])
        duration = (
            timezone.datetime.fromisoformat(booking_data["end_date"]).date()
            - timezone.datetime.fromisoformat(booking_data["start_date"]).date()
        ).days

        total = stay_price * duration * len(booking_data["pet_ids"])

        # Add services if any
        if "selected_services" in booking_data:
            for pet_id, services in booking_data["selected_services"].items():
                for service_id, quantity in services.items():
                    service = toconline.get(TocOnlineResource.SERVICES, pk=service_id)
                    service_price = Decimal(service["attributes"]["sales_price"])
                    total += service_price * quantity

        context["total_amount"] = total
        return context

    def form_valid(self, form):
        booking_data = self.request.session.get('booking_data', {})
        if not booking_data:
            return redirect('hotel:booking_stay')

        # Create the booking record
        booking = self.create_booking(form)

        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            # Create Stripe Checkout Session
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'eur',
                        'product_data': {
                            'name': f'Pet Hotel Booking #{booking.id}',
                        },
                        'unit_amount': int(booking.total_price_eur * 100),
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=self.request.build_absolute_uri(
                    reverse_lazy('hotel:booking_payment_verify', kwargs={'booking_id': booking.id})
                ) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=self.request.build_absolute_uri(
                    reverse_lazy('hotel:booking_retry', kwargs={'booking_id': booking.id})
                ),
                customer_email=self.request.user.email,
                metadata={
                    'booking_id': str(booking.id),
                    'account_id': str(self.request.user.account.id),
                },
            )

            # Save Stripe session ID to booking
            booking.stripe_session_id = checkout_session.id
            booking.save()

            return redirect(checkout_session.url)

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error: {str(e)}")
            messages.error(self.request,
                           "Payment processing error. Please try again.")
            return redirect('hotel:booking_review')

    def create_booking(self, form):
        booking_data = self.request.session["booking_data"]

        # Create booking
        booking = models.Booking.objects.create(
            account=self.request.user.account,
            status="pending",
            notes=form.cleaned_data.get("special_requests", ""),
        )

        # Get stay details
        stay_service = toconline.get(
            TocOnlineResource.SERVICES, pk=booking_data["stay_id"]
        )

        # Create stays for each pet
        for pet_id in booking_data["pet_ids"]:
            pet = Pet.objects.get(pk=pet_id, owner=self.request.user.account)

            # Create booking stay
            stay = models.BookingStay.objects.create(
                booking=booking,
                pet=pet,
                stay_id=stay_service["id"],
                stay_name=stay_service["attributes"]["item_description"],
                unit_price_eur=Decimal(stay_service["attributes"]["sales_price"]),
                start_date=booking_data["start_date"],
                end_date=booking_data["end_date"],
            )

            # Add services if any
            if str(pet_id) in booking_data.get("selected_services", {}):
                for service_id, quantity in booking_data["selected_services"][
                    str(pet_id)
                ].items():
                    service = toconline.get(TocOnlineResource.SERVICES, pk=service_id)

                    models.BookingService.objects.create(
                        stay=stay,
                        pet=pet,
                        service_id=service["id"],
                        service_name=service["attributes"]["item_description"],
                        service_type=models.ServiceType.FIXED,
                        unit_price_eur=Decimal(service["attributes"]["sales_price"]),
                        quantity=quantity,
                    )

        self.request.session["booking_data"]["id"] = booking.pk
        return booking


class BookingPaymentVerifyView(LoginRequiredMixin, View):
    def get(self, request, booking_id):
        booking = get_object_or_404(
            models.Booking,
            id=booking_id,
            account=request.user.account
        )

        if booking.status == models.BookingStatus.PAID:
            return redirect('hotel:booking_success', booking_id=booking.id)

        session_id = request.GET.get('session_id')

        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            if session_id:
                # Check the provided session
                session = stripe.checkout.Session.retrieve(session_id)
                if session.payment_status == 'paid' and session.metadata.get('booking_id') == str(booking.id):
                    booking.status = models.BookingStatus.PAID
                    booking.paid_at = timezone.now()
                    booking.stripe_session_id = session_id
                    booking.save()
                    return redirect('hotel:booking_success', booking_id=booking.id)

            # Fallback check for existing session
            if booking.stripe_session_id:
                session = stripe.checkout.Session.retrieve(booking.stripe_session_id)
                if session.payment_status == 'paid':
                    booking.status = models.BookingStatus.PAID
                    booking.paid_at = timezone.now()
                    booking.save()
                    return redirect('hotel:booking_success', booking_id=booking.id)

        except stripe.error.StripeError as e:
            logger.error(f"Stripe verification error: {str(e)}")

        # If payment not confirmed, allow retry
        return redirect('hotel:booking_retry', booking_id=booking.id)


class BookingRetryView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_retry.html"

    def dispatch(self, request, *args, **kwargs):
        booking = get_object_or_404(
            models.Booking,
            id=kwargs['booking_id'],
            account=request.user.account
        )
        
        # Check if any stay is in the past
        if any(stay.end_date < timezone.now().date() for stay in booking.stays.all()):
            messages.error(request, "This booking can no longer be paid as the stay dates have passed")
            return redirect('hotel:booking_list')
            
        if booking.status != models.BookingStatus.PENDING:
            messages.error(request, "This booking doesn't require payment")
            return redirect('hotel:booking_list')
            
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking = get_object_or_404(
            models.Booking,
            id=kwargs['booking_id'],
            account=self.request.user.account,
            status=models.BookingStatus.PENDING
        )

        # Create a new Stripe session if needed
        stripe.api_key = settings.STRIPE_SECRET_KEY
        
        try:
            # Check if existing session is still valid
            if booking.stripe_session_id:
                session = stripe.checkout.Session.retrieve(booking.stripe_session_id)
                if session.payment_status == 'paid':
                    # Payment was completed, update booking status
                    booking.status = models.BookingStatus.PAID
                    booking.paid_at = timezone.now()
                    booking.save()
                    return redirect('hotel:booking_success', booking_id=booking.id)
                
                if session.status == 'open':
                    # Existing session is still valid
                    context['payment_url'] = session.url
                    context['booking'] = booking
                    return context

            # Create a new Stripe session
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'eur',
                        'product_data': {
                            'name': f'Pet Hotel Booking #{booking.id}',
                        },
                        'unit_amount': int(booking.total_price_eur * 100),
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=self.request.build_absolute_uri(
                    reverse_lazy('hotel:booking_payment_verify', kwargs={'booking_id': booking.id})
                ) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=self.request.build_absolute_uri(
                    reverse_lazy('hotel:booking_retry', kwargs={'booking_id': booking.id})
                ),
                customer_email=self.request.user.email,
                metadata={
                    'booking_id': str(booking.id),
                    'account_id': str(self.request.user.account.id),
                },
            )

            # Update booking with new session ID
            booking.stripe_session_id = checkout_session.id
            booking.save()

            context['payment_url'] = checkout_session.url
            context['booking'] = booking
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error during retry: {str(e)}")
            messages.error(self.request, "Error creating payment session. Please try again later.")
            return redirect('hotel:booking_list')

        return context


class BookingSuccessView(LoginRequiredMixin, TemplateView):
    template_name = 'hotel/booking_success.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking_id = kwargs.get('booking_id')
        
        try:
            booking = models.Booking.objects.get(
                id=booking_id,
                account=self.request.user.account
            )
            context['booking'] = booking
            
            # Clear session booking data after successful payment
            if 'booking_data' in self.request.session:
                del self.request.session['booking_data']
                
        except models.Booking.DoesNotExist:
            raise Http404("Booking not found")
        
        return context


class HotelBookingListView(LoginRequiredMixin, TemplateView):
    template_name = "hotel/booking_list.html"
    paginate_by = 9

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now().date()

        # Get filter from query params
        status_filter = self.request.GET.get('status', 'all')

        # Base queryset
        bookings = models.Booking.objects.filter(
            account=self.request.user.account
        ).prefetch_related('stays').order_by('-created_at')

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

        if not self.request.user or not self.request.user.is_staff:
            return qs.filter(account__user=self.request.user)

        return qs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking = self.object
        
        # Add payment URL if booking is pending
        if booking.status == models.BookingStatus.PENDING:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            
            try:
                # Check if existing session is still valid
                if booking.stripe_session_id:
                    session = stripe.checkout.Session.retrieve(booking.stripe_session_id)
                    if session.payment_status == 'paid':
                        # Payment was completed, update booking status
                        booking.status = models.BookingStatus.PAID
                        booking.paid_at = timezone.now()
                        booking.save()
                        return redirect('hotel:booking_success', booking_id=booking.id)
                    
                    if session.status == 'open':
                        # Existing session is still valid
                        context['payment_url'] = session.url
                        return context

                # Create a new Stripe session if needed
                checkout_session = stripe.checkout.Session.create(
                    payment_method_types=['card'],
                    line_items=[{
                        'price_data': {
                            'currency': 'eur',
                            'product_data': {
                                'name': f'Pet Hotel Booking #{booking.id}',
                            },
                            'unit_amount': int(booking.total_price_eur * 100),
                        },
                        'quantity': 1,
                    }],
                    mode='payment',
                    success_url=self.request.build_absolute_uri(
                        reverse_lazy('hotel:booking_payment_verify', kwargs={'booking_id': booking.id})
                    ) + '?session_id={CHECKOUT_SESSION_ID}',
                    cancel_url=self.request.build_absolute_uri(
                        reverse_lazy('hotel:booking_retry', kwargs={'booking_id': booking.id})
                    ),
                    customer_email=self.request.user.email,
                    metadata={
                        'booking_id': str(booking.id),
                        'account_id': str(self.request.user.account.id),
                    },
                )

                # Update booking with new session ID
                booking.stripe_session_id = checkout_session.id
                booking.save()

                context['payment_url'] = checkout_session.url
                
            except stripe.error.StripeError as e:
                logger.error(f"Stripe error in booking detail: {str(e)}")
                messages.error(self.request, "Error creating payment session. Please try again later.")

        # Add additional context if needed
        context['page_title'] = f"Booking #{self.object.id}"
        
        return context
    
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionDenied:
            # Custom handling if you want to show a different template
            return self.handle_no_permission()
