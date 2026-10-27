from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.corecode.models import AcademicSession, AcademicTerm, StudentClass
from apps.finance.models import Invoice, InvoiceItem, Receipt
from apps.finance.models import PaymentAllocation
from apps.students.models import Student


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ("description", "amount")


class ReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Receipt
        fields = ("id", "amount_paid", "date_paid", "reference_number", "payment_method", "comment")


class PaymentAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAllocation
        fields = ("id", "receipt", "invoice_item", "amount", "note", "created_at", "created_by")


class InvoiceSerializer(serializers.ModelSerializer):
    """API contract for invoice create/detail/list responses using actual model field names."""

    registration_number = serializers.CharField(write_only=True)
    session = serializers.CharField(write_only=True)
    term = serializers.CharField(write_only=True)
    class_for = serializers.CharField(write_only=True)
    items = InvoiceItemSerializer(many=True, required=False, write_only=True)
    balance = serializers.SerializerMethodField(read_only=True)
    total_amount_payable = serializers.SerializerMethodField(read_only=True)
    total_amount_paid = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Invoice
        fields = (
            "id",
            "registration_number",
            "session",
            "term",
            "class_for",
            "balance_from_previous_term",
            "status",
            "items",
            "balance",
            "total_amount_payable",
            "total_amount_paid",
        )
        read_only_fields = ("id", "balance", "total_amount_payable", "total_amount_paid")

    def get_balance(self, obj):
        return obj.balance()

    def get_total_amount_payable(self, obj):
        return obj.total_amount_payable()

    def get_total_amount_paid(self, obj):
        return obj.total_amount_paid()

    def validate(self, attrs):
        registration_number = attrs.get("registration_number")
        session_name = attrs.get("session")
        term_name = attrs.get("term")
        class_for_name = attrs.get("class_for")

        if not registration_number:
            raise serializers.ValidationError(
                {"registration_number": "This field is required."}
            )
        if not session_name:
            raise serializers.ValidationError({"session": "This field is required."})
        if not term_name:
            raise serializers.ValidationError({"term": "This field is required."})
        if not class_for_name:
            raise serializers.ValidationError({"class_for": "This field is required."})

        try:
            Student.objects.get(registration_number=registration_number)
        except Student.DoesNotExist:
            raise serializers.ValidationError(
                {"registration_number": "No Student exists for this registration_number."}
            )

        if not AcademicSession.objects.filter(name=session_name).exists():
            raise serializers.ValidationError(
                {"session": "No AcademicSession matches the provided session name."}
            )
        if not AcademicTerm.objects.filter(name=term_name).exists():
            raise serializers.ValidationError(
                {"term": "No AcademicTerm matches the provided term name."}
            )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        registration_number = validated_data.pop("registration_number")
        session_name = validated_data.pop("session")
        term_name = validated_data.pop("term")
        class_for_name = validated_data.pop("class_for")
        items_data = validated_data.pop("items", [])

        student = Student.objects.get(registration_number=registration_number)
        session = AcademicSession.objects.get(name=session_name)
        term = AcademicTerm.objects.get(name=term_name)

        class_obj, created = StudentClass.objects.get_or_create(name=class_for_name)
        invoice = Invoice.objects.create(
            student=student,
            session=session,
            term=term,
            class_for=class_obj,
            balance_from_previous_term=validated_data.get(
                "balance_from_previous_term", 0
            ),
            status=validated_data.get("status", "active"),
        )

        for item_data in items_data:
            InvoiceItem.objects.create(invoice=invoice, **item_data)

        return invoice
