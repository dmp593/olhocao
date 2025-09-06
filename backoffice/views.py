from datetime import timedelta

from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.dateparse import parse_date
from django.views.generic import (
    TemplateView,
    ListView,
    DeleteView,
    DetailView,
    UpdateView,
)
from django.utils import timezone
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.shortcuts import redirect, render
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.views.generic.edit import FormView
from django.views import View


from olhocao.mixins import (
    StaffRequiredMixin, LoginRequiredMixin, UserPassesTestMixin
)

from hotel.models import (
    BookingStay, BookingStatus
)

from hotel import models as hotel_models

from pets.models import Pet
from accounts.forms import UserChangeForm
from frontoffice.models import ContactRequest

from .models import LegalDocument

from .forms import (
    LegalDocumentForm,
    create_section_formset,
    create_lineitem_formset,
)


class DashboardView(StaffRequiredMixin, TemplateView):
    template_name = 'backoffice/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()

        # Get and validate dates from request
        start_date = parse_date(
            self.request.GET.get('start_date', today.isoformat())
        )
        end_date = parse_date(
            self.request.GET.get('end_date', today.isoformat())
        )
        
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        # Calculate navigation dates
        duration = (end_date - start_date).days + 1
        previous_start = start_date - timedelta(days=duration)
        previous_end = previous_start + (end_date - start_date)
        next_start = start_date + timedelta(days=duration)
        next_end = next_start + (end_date - start_date)

        # Get stays overlapping with date range
        stays = BookingStay.objects.filter(
            start_date__lte=end_date,
            end_date__gte=start_date,
            booking__status=BookingStatus.PAID,
        ).select_related('booking', 'pet').order_by('start_date')

        # Quick stats
        checkins = stays.filter(start_date__range=[start_date, end_date])
        checkouts = stays.filter(end_date__range=[start_date, end_date])
        active = stays
        stats = {
            'checkins_count': checkins.count(),
            'checkouts_count': checkouts.count(),
            'active_count': active.count(),
            'revenue_cents': sum(s.total_price_eur for s in active),
        }

        context.update({
            'start_date': start_date,
            'end_date': end_date,
            'checkins': checkins,
            'checkouts': checkouts,
            'stays': active,
            'previous_start': previous_start,
            'previous_end': previous_end,
            'next_start': next_start,
            'next_end': next_end,
            'stats': stats,
        })
        return context


class UsersListView(StaffRequiredMixin, ListView):
    template_name = 'backoffice/users_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        User = get_user_model()
        return (
            User.objects
            .select_related('account')
            .order_by('first_name', 'last_name')
        )


class UserDetailView(LoginRequiredMixin, DetailView):
    template_name = 'backoffice/user_detail.html'
    context_object_name = 'user_obj'

    def get_queryset(self):
        User = get_user_model()
        return User.objects.select_related('account')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.object
        toconline = getattr(
            getattr(user, 'account', None),
            'toconline_customer',
            None,
        )

        bookings_qs = (
            hotel_models.Booking.objects
            .filter(account__user=user)
            .prefetch_related('stays')
            .order_by('-created_at')
        )

        paginator = Paginator(bookings_qs, 10)
        page_number = self.request.GET.get('page')
        bookings_page = paginator.get_page(page_number)

        # Fetch user's pets (no pagination needed)
        user_pets = (
            Pet.objects.filter(owner__user=user)
            .order_by('name')
        )

        context.update({
            'account': getattr(user, 'account', None),
            'toconline_customer': toconline,
            'bookings': bookings_page.object_list,
            'page_obj': bookings_page,
            'paginator': paginator,
            'is_paginated': bookings_page.has_other_pages(),
            'user_pets': user_pets,
            'current_date': timezone.now().date(),
        })
        return context


class UserCreateView(StaffRequiredMixin, FormView):
    template_name = 'backoffice/user_create.html'
    success_url = None  # computed in form_valid

    def get_form_class(self):
        from accounts.forms import SignUpForm
        return SignUpForm

    def form_valid(self, form):
        # Do NOT log in as the created user
        if form.is_valid():
            user = form.save()
            messages.success(self.request, _("User created."))
            return redirect('backoffice:user_detail', pk=user.pk)
        return super().form_invalid(form)


class UserUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    template_name = 'backoffice/user_form.html'
    form_class = UserChangeForm

    def test_func(self):
        user_pk = self.kwargs.get('pk', self.request.user.pk)

        if self.request.user.is_staff:
            return True

        return self.request.user.pk == user_pk

    def get_queryset(self):
        User = get_user_model()
        return User.objects.select_related('account')

    def get_object(self, queryset=None):
        User = get_user_model()
        user_pk = self.kwargs.get('pk', self.request.user.pk)

        return User.objects.select_related('account').get(pk=user_pk)

    def get_initial(self):
        data = super().get_initial()
        account = self.get_object().account

        if account and account.has_toconline_customer:
            customer = account.toconline_customer or {}
            attrs = customer.get('attributes', {})
            vat = attrs.get('tax_registration_number')
            phone = attrs.get('mobile_number') or attrs.get('phone_number')
            if vat:
                data['vat'] = vat
            if phone:
                data['phone'] = phone
        else:
            # Best-effort fallback for phone via property if available
            if account and account.phone_number:
                data['phone'] = account.phone_number

        return data

    def get_success_url(self):
        return (
            self.request.GET.get('next')
            or self.request.POST.get('next')
            or reverse_lazy(
                'backoffice:user_detail',
                kwargs={'pk': self.object.pk}
            )
        )


class LegalDocumentListView(ListView):
    queryset = LegalDocument.objects.filter(deleted_at__isnull=True).all()
    context_object_name = 'legal_documents'


class LegalDocumentCreateOrUpdateView(StaffRequiredMixin, TemplateView):
    template_name = 'backoffice/legaldocument_form.html'
    success_url = reverse_lazy('backoffice:legal_documents')

    def get(self, request, pk=None):
        legal_doc = None

        if pk:
            try:
                legal_doc = LegalDocument.objects_active.get(pk=pk)
            except LegalDocument.DoesNotExist:
                return redirect('backoffice:legal_document_create')

        doc_form = LegalDocumentForm(instance=legal_doc)

        SectionFormSet = create_section_formset()
        LineItemFormSet = create_lineitem_formset()

        section_formset = SectionFormSet(
            instance=legal_doc,
            prefix='sections'
        )

        lineitem_formsets = [
            LineItemFormSet(
                instance=section_form.instance,
                prefix=f'lineitems-{i}'
            )
            for i, section_form in enumerate(section_formset)
        ]

        return render(request, self.template_name, {
            'object': legal_doc,
            'doc_form': doc_form,
            'section_formset': section_formset,
            'lineitem_formsets': lineitem_formsets,
        })

    def post(self, request, pk=None):
        legal_doc = None

        if pk:
            try:
                legal_doc = LegalDocument.objects_active.get(pk=pk)
            except LegalDocument.DoesNotExist:
                
                messages.error(
                    self.request,
                    _('Legal document not found.')
                )

                return redirect('backoffice:legal_documents')
        
        doc_form = LegalDocumentForm(request.POST, instance=legal_doc)

        SectionFormSet = create_section_formset()
        LineItemFormSet = create_lineitem_formset()

        section_formset = SectionFormSet(
            data=request.POST,
            instance=doc_form.instance,
            prefix='sections'
        )

        lineitem_formsets = [
            LineItemFormSet(
                request.POST,
                instance=section_form.instance,
                prefix=f'lineitems-{i}',
            )
            for i, section_form in enumerate(section_formset)
        ]

        forms_valid = (
            doc_form.is_valid()
            and section_formset.is_valid()
            and all(f.is_valid() for f in lineitem_formsets)
        )

        if not forms_valid:
            return render(
                request,
                self.template_name,
                {
                    'object': doc_form.instance,
                    'doc_form': doc_form,
                    'section_formset': section_formset,
                    'lineitem_formsets': lineitem_formsets,
                },
            )

        doc_form.save()
        section_formset.save()

        for lineitem_formset in lineitem_formsets:
            lineitem_formset.save()

        success_msg = (
            _("Legal document created.")
            if pk is None
            else _("Legal document updated.")
        )
        messages.success(self.request, success_msg)

        return redirect(self.success_url)


class LegalDocumentDeleteView(StaffRequiredMixin, DeleteView):
    queryset = LegalDocument.objects.all()
    success_url = reverse_lazy('backoffice:legal_documents')

    def form_valid(self, form):
        success_url = self.get_success_url()

        self.object.deleted_at = timezone.now()
        self.object.save()

        messages.success(
            self.request,
            _('Legal Document deleted.')
        )

        return HttpResponseRedirect(success_url)


class ContactRequestListView(StaffRequiredMixin, ListView):
    model = ContactRequest
    context_object_name = 'contacts_requests'
    template_name = 'backoffice/contactrequest_list.html'
    paginate_by = 10


class ContactRequestMarkReadView(StaffRequiredMixin, View):
    def post(self, request):
        ids = request.POST.getlist('selected_requests')

        if ids:
            updated = ContactRequest.objects.filter(
                id__in=ids,
                read_at__isnull=True
            )
            updated_count = updated.update(read_at=timezone.now())
            if updated_count:
                messages.success(request, _("Marked as read."))
            else:
                messages.info(request, _("No unread requests selected."))
        else:
            messages.warning(request, _("No requests selected."))

        return redirect('backoffice:contacts_requests')


class UserToggleActiveView(StaffRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_authenticated or not request.user.is_staff:
            messages.error(request, _("Unauthorized"))
            return redirect('backoffice:dashboard')

        User = get_user_model()
        target = User.objects.filter(pk=pk).first()
        if not target:
            messages.error(request, _("User not found"))
            return redirect('backoffice:users_list')

        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])

        msg = (
            _("User deactivated")
            if not target.is_active
            else _("User activated")
        )
        messages.success(request, msg)

        next_url = (
            request.POST.get('next')
            or request.META.get('HTTP_REFERER')
            or reverse_lazy('backoffice:user_detail', kwargs={'pk': pk})
        )

        return redirect(next_url)


class BookHotelForUserView(StaffRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_authenticated or not request.user.is_staff:
            messages.error(request, _("Unauthorized"))
            return redirect('backoffice:dashboard')

        # Mark in session that admin is acting on behalf of this user
        request.session['acting_user_id'] = pk

        messages.info(
            request,
            _("Booking will be created on behalf of selected user."),
        )

        return redirect('hotel:booking_stay')
