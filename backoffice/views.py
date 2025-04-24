from datetime import timedelta

from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.dateparse import parse_date
from django.views.generic import TemplateView, ListView, DeleteView
from django.utils import timezone
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.shortcuts import redirect, render

from hotel.models import BookingStay

from .models import LegalDocument

from .forms import (
    LegalDocumentForm,
    create_section_formset,
    create_lineitem_formset,
)


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
    queryset = LegalDocument.objects.filter(deleted_at__isnull=True).all()
    context_object_name = 'legal_documents'


class LegalDocumentCreateOrUpdateView(TemplateView):
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


class LegalDocumentDeleteView(DeleteView):
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
