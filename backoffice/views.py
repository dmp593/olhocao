from datetime import timedelta
from django.utils.dateparse import parse_date
from django.views.generic import TemplateView
from django.utils import timezone
from django.views.generic import ListView
from django.urls import reverse_lazy
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.shortcuts import get_object_or_404, redirect, render

from hotel.models import BookingStay

from .models import LegalDocument
from .forms import LegalDocumentForm

from .forms import (
    LegalDocumentForm,
    create_section_formset,
    create_lineitem_formset,
)

from .models import LegalDocument, LegalDocumentSection


class DashboardView(TemplateView):
    template_name = 'backoffice/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()

        # Get and validate dates from request
        start_date = parse_date(self.request.GET.get('start_date', today.isoformat()))
        end_date = parse_date(self.request.GET.get('end_date', today.isoformat()))
        
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
            end_date__gte=start_date
        ).select_related('booking', 'pet').order_by('start_date')

        context.update({
            'start_date': start_date,
            'end_date': end_date,
            'checkins': stays.filter(start_date__range=[start_date, end_date]),
            'checkouts': stays.filter(end_date__range=[start_date, end_date]),
            'stays': stays,
            'previous_start': previous_start,
            'previous_end': previous_end,
            'next_start': next_start,
            'next_end': next_end,
        })
        return context


class LegalDocumentListView(ListView):
    model = LegalDocument
    context_object_name = 'legal_documents'


class LegalDocumentCreateOrUpdateView(TemplateView):
    template_name = 'backoffice/legaldocument_form.html'
    success_url = reverse_lazy('backoffice:legal_documents')

    def get(self, request, pk=None):
        doc_form = LegalDocumentForm(
            instance=get_object_or_404(LegalDocument, pk=pk) if pk else None
        )

        SectionFormSet = create_section_formset(extra=0 if pk else 1)
        LineItemFormSet = create_lineitem_formset(extra=0 if pk else 1)

        section_formset = SectionFormSet(
            instance=doc_form.instance,
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
            'object': doc_form.instance,
            'doc_form': doc_form,
            'section_formset': section_formset,
            'lineitem_formsets': lineitem_formsets,
        })

    def post(self, request, pk=None):
        doc_form = LegalDocumentForm(
            request.POST,
            instance=get_object_or_404(LegalDocument, pk=pk) if pk else None
        )

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

        if not doc_form.is_valid() or not section_formset.is_valid() or any([not f.is_valid() for f in lineitem_formsets]):
            return render(request, self.template_name, {
                'object': doc_form.instance,
                'doc_form': doc_form,
                'section_formset': section_formset,
                'lineitem_formsets': lineitem_formsets,
            })

        doc_form.save()
        section_formset.save()
        
        for lineitem_formset in lineitem_formsets:
            lineitem_formset.save()

        messages.success(
            self.request,
            _("Legal document created." if pk is None else "Legal document updated.")
        )

        return redirect(self.success_url)
