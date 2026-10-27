import json
import logging

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from apps.corecode.authentication import ExternalAPIKeyAuthentication
from apps.corecode.models import AcademicSession, AcademicTerm, StudentClass
from apps.finance.models import Invoice, InvoiceItem, Receipt
from apps.finance.serializers import InvoiceSerializer, InvoiceItemSerializer, ReceiptSerializer
from apps.students.models import Student
from apps.students.serializers import StudentSyncSerializer

logger = logging.getLogger(__name__)


class StudentSyncAPIView(APIView):
    authentication_classes = [ExternalAPIKeyAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        registration_number = request.data.get("registration_number")
        existing = Student.objects.filter(registration_number=registration_number).first() if registration_number else None

        if existing:
            serializer = StudentSyncSerializer(existing, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            student = serializer.save()
            action = "updated"
            status_code = status.HTTP_200_OK
        else:
            serializer = StudentSyncSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            student = serializer.save()
            action = "created"
            status_code = status.HTTP_201_CREATED

        return Response(
            {
                "message": f"Student {action}",
                "registration_number": student.registration_number,
                "surname": student.surname,
                "firstname": student.firstname,
                "other_name": student.other_name,
                "gender": student.gender,
                "current_class": student.current_class.name if student.current_class else None,
                "parent_mobile_number": student.parent_mobile_number,
            },
            status=status_code,
        )


class UserSyncAPIView(APIView):
    authentication_classes = [ExternalAPIKeyAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email")
        if not email:
            return Response({"email": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)

        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "first_name": request.data.get("first_name", ""),
                "last_name": request.data.get("last_name", ""),
            },
        )

        if created:
            user.set_unusable_password()
            user.is_active = True
            user.save(update_fields=["password", "is_active"])
        else:
            updated = False
            if request.data.get("first_name") is not None and user.first_name != request.data.get("first_name"):
                user.first_name = request.data.get("first_name")
                updated = True
            if request.data.get("last_name") is not None and user.last_name != request.data.get("last_name"):
                user.last_name = request.data.get("last_name")
                updated = True
            if updated:
                user.save(update_fields=["first_name", "last_name"])

        payload = {
            "message": "User created" if created else "User already exists",
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "created": created,
        }
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class InvoiceListCreateAPIView(APIView):
    authentication_classes = [ExternalAPIKeyAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        registration_number = request.query_params.get("registration_number")
        if registration_number:
            invoices = Invoice.objects.filter(student__registration_number=registration_number)
        else:
            invoices = Invoice.objects.all()

        payload = []
        for invoice in invoices:
            payload.append(
                {
                    "id": invoice.pk,
                    "registration_number": invoice.student.registration_number,
                    "session": invoice.session.name,
                    "term": invoice.term.name,
                    "class_for": invoice.class_for.name,
                    "balance_from_previous_term": invoice.balance_from_previous_term,
                    "status": invoice.status,
                    "balance": invoice.balance(),
                    "total_amount_payable": invoice.total_amount_payable(),
                    "total_amount_paid": invoice.total_amount_paid(),
                    "items": [
                        {
                            "id": item.pk,
                            "description": item.description,
                            "amount": item.amount,
                        }
                        for item in InvoiceItem.objects.filter(invoice=invoice)
                    ],
                    "receipts": [
                        {
                            "id": receipt.pk,
                            "amount_paid": receipt.amount_paid,
                            "date_paid": receipt.date_paid,
                            "comment": receipt.comment,
                        }
                        for receipt in Receipt.objects.filter(invoice=invoice)
                    ],
                }
            )
        return Response(payload, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = InvoiceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        invoice = serializer.save()
        response = {
            "id": invoice.pk,
            "registration_number": invoice.student.registration_number,
            "session": invoice.session.name,
            "term": invoice.term.name,
            "class_for": invoice.class_for.name,
            "balance_from_previous_term": invoice.balance_from_previous_term,
            "status": invoice.status,
            "balance": invoice.balance(),
            "total_amount_payable": invoice.total_amount_payable(),
            "total_amount_paid": invoice.total_amount_paid(),
            "items": InvoiceItemSerializer(InvoiceItem.objects.filter(invoice=invoice), many=True).data,
        }
        return Response(response, status=status.HTTP_201_CREATED)


class InvoiceDetailAPIView(APIView):
    authentication_classes = [ExternalAPIKeyAuthentication]
    permission_classes = [AllowAny]

    def get(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        receipts = Receipt.objects.filter(invoice=invoice)
        items = InvoiceItem.objects.filter(invoice=invoice)

        payload = {
            "id": invoice.pk,
            "registration_number": invoice.student.registration_number,
            "session": invoice.session.name,
            "term": invoice.term.name,
            "class_for": invoice.class_for.name,
            "balance_from_previous_term": invoice.balance_from_previous_term,
            "status": invoice.status,
            "balance": invoice.balance(),
            "total_amount_payable": invoice.total_amount_payable(),
            "total_amount_paid": invoice.total_amount_paid(),
            "items": InvoiceItemSerializer(items, many=True).data,
            "receipts": ReceiptSerializer(receipts, many=True).data,
        }
        return Response(payload, status=status.HTTP_200_OK)
