from django.urls import path

from backoffice import views


app_name = 'backoffice'


urlpatterns = [
    path(
        'dashboard/',
        views.DashboardView.as_view(),
        name='dashboard'
    ),

    path(
        'legal-documents/',
        views.LegalDocumentListView.as_view(),
        name='legal_documents'
    ),

    path(
        'legal-documents/create/',
        views.LegalDocumentCreateOrUpdateView.as_view(),
        name='legal_document_create'
    ),

    path(
        'legal-documents/<int:pk>/edit/',
        views.LegalDocumentCreateOrUpdateView.as_view(),
        name='legal_document_update'
    ),

    path(
        'legal-documents/<int:pk>/delete/',
        views.LegalDocumentDeleteView.as_view(),
        name='legal_document_delete'
    )
]
