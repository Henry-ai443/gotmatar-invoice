from django.urls import path

from .api_views import InvoiceDetailAPIView, InvoiceListCreateAPIView, StudentSyncAPIView, UserSyncAPIView

urlpatterns = [
    path("students/sync/", StudentSyncAPIView.as_view(), name="api-student-sync"),
    path("users/sync/", UserSyncAPIView.as_view(), name="api-user-sync"),
    path("invoices/", InvoiceListCreateAPIView.as_view(), name="api-invoice-list-create"),
    path("invoices/<int:pk>/", InvoiceDetailAPIView.as_view(), name="api-invoice-detail"),
]
