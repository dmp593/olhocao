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
        'users/',
        views.UsersListView.as_view(),
        name='users_list'
    ),
    path(
        'users/<int:pk>/',
        views.UserDetailView.as_view(),
        name='user_detail'
    ),
    path(
        'users/<int:pk>/edit/',
        views.UserAdminUpdateView.as_view(),
        name='user_edit'
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
    ),

    path(
        'contacts-requests/',
        views.ContactRequestListView.as_view(),
        name='contacts_requests'
    ),
    path(
        'contacts-requests/mark-read/',
        views.ContactRequestMarkReadView.as_view(),
        name='contactrequest_mark_read'
    ),
]
