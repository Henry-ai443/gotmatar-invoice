from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.corecode.models import AcademicSession, AcademicTerm, StudentClass
from apps.students.models import Student
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from decimal import Decimal


class Invoice(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    term = models.ForeignKey(AcademicTerm, on_delete=models.CASCADE)
    class_for = models.ForeignKey(StudentClass, on_delete=models.CASCADE)
    balance_from_previous_term = models.IntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=[("active", "Active"), ("closed", "Closed")],
        default="active",
    )

    class Meta:
        ordering = ["student", "term"]

    def __str__(self):
        return f"{self.student}"

    def balance(self):
        payable = Decimal(self.total_amount_payable())
        paid = Decimal(self.total_amount_paid())
        bal = payable - paid
        return bal if bal >= Decimal('0.00') else Decimal('0.00')

    def amount_payable(self):
        items = InvoiceItem.objects.filter(invoice=self)
        total = 0
        for item in items:
            total += item.amount
        return total

    def total_amount_payable(self):
        return self.balance_from_previous_term + self.amount_payable()

    def total_amount_paid(self):
        receipts = Receipt.objects.filter(invoice=self)
        amount = 0
        for receipt in receipts:
            amount += receipt.amount_paid
        return amount

    def get_absolute_url(self):
        return reverse("invoice-detail", kwargs={"pk": self.pk})


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    description = models.CharField(max_length=200)
    amount = models.IntegerField()
    vote_head = models.ForeignKey(
        "VoteHead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_items",
    )

    def allocated_total(self):
        total = self.allocations.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        return Decimal(total)

    def outstanding_amount(self):
        # amount is stored as IntegerField historically
        amt = Decimal(self.amount)
        return amt - self.allocated_total()


class Receipt(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    date_paid = models.DateField(default=timezone.now)
    comment = models.CharField(max_length=200, blank=True)
    reference_number = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    PAYMENT_METHOD_CHOICES = [
        ("CASH", "Cash"),
        ("MPESA", "M-Pesa"),
        ("BANK", "Bank Transfer"),
        ("CHEQUE", "Cheque"),
        ("OTHER", "Other"),
    ]
    payment_method = models.CharField(max_length=32, choices=PAYMENT_METHOD_CHOICES, null=True, blank=True, db_index=True)

    def __str__(self):
        return f"Receipt on {self.date_paid}"

    def allocated_total(self):
        total = self.allocations.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        return Decimal(total)

    def unallocated_amount(self):
        return Decimal(self.amount_paid) - self.allocated_total()

    def save(self, *args, **kwargs):
        # Prevent reducing amount_paid below already allocated total
        if self.pk:
            allocated = self.allocations.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            if Decimal(self.amount_paid) < Decimal(allocated):
                raise ValidationError(f"Cannot set amount_paid below already allocated total ({allocated})")
        super().save(*args, **kwargs)


class PaymentAllocation(models.Model):
    receipt = models.ForeignKey(
        Receipt,
        on_delete=models.CASCADE,
        related_name='allocations',
        db_index=True,
    )
    invoice_item = models.ForeignKey(
        InvoiceItem,
        on_delete=models.PROTECT,
        related_name='allocations',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [
            models.Index(fields=['invoice_item', 'receipt']),
        ]

    def __str__(self):
        return f"Alloc {self.amount} -> item={self.invoice_item_id} (receipt={self.receipt_id})"

    def clean(self):
        # Basic validation: allocations must be strictly greater than 0.00
        if self.amount is None:
            raise ValidationError("Allocation amount is required")
        if Decimal(self.amount) <= Decimal('0.00'):
            raise ValidationError("Allocation amount must be greater than 0.00")
        # Ensure the invoice_item belongs to the same invoice as the receipt
        if self.invoice_item_id and self.receipt_id:
            try:
                item = InvoiceItem.objects.get(pk=self.invoice_item_id)
                receipt = Receipt.objects.get(pk=self.receipt_id)
                if item.invoice_id != receipt.invoice_id:
                    raise ValidationError("InvoiceItem does not belong to the Receipt's invoice")
            except InvoiceItem.DoesNotExist:
                raise ValidationError("InvoiceItem does not exist")
            except Receipt.DoesNotExist:
                raise ValidationError("Receipt does not exist")

    def save(self, *args, **kwargs):
        # Centralized transactional validation and save with row-level locking
        from django.db import IntegrityError

        self.clean()

        with transaction.atomic():
            # Lock the receipt row to prevent concurrent allocations exceeding receipt.amount_paid
            receipt = Receipt.objects.select_for_update().get(pk=self.receipt_id)

            # If invoice_item is set, lock it as well to prevent concurrent over-allocation
            item = None
            if self.invoice_item_id:
                item = InvoiceItem.objects.select_for_update().get(pk=self.invoice_item_id)

            # Compute current allocations for this receipt excluding this instance (if updating)
            existing_alloc_sum = PaymentAllocation.objects.filter(receipt=receipt).exclude(pk=self.pk).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            existing_alloc_sum = Decimal(existing_alloc_sum)

            # Ensure receipt.amount_paid is Decimal
            receipt_amount = Decimal(receipt.amount_paid)

            if existing_alloc_sum + Decimal(self.amount) > receipt_amount:
                raise ValidationError("Total allocations for this receipt would exceed the receipt amount")

            # If allocated to an invoice item, ensure not exceeding the invoice_item outstanding amount
            if item:
                item_alloc_sum = PaymentAllocation.objects.filter(invoice_item=item).exclude(pk=self.pk).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                item_alloc_sum = Decimal(item_alloc_sum)
                item_amount = Decimal(item.amount)
                remaining = item_amount - item_alloc_sum
                if Decimal(self.amount) > remaining:
                    raise ValidationError("Allocation exceeds the invoice item outstanding amount")

            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Ensure deletion occurs within a transaction and locks parent receipt and item
        with transaction.atomic():
            # Lock receipt and item if present
            Receipt.objects.select_for_update().filter(pk=self.receipt_id)
            if self.invoice_item_id:
                InvoiceItem.objects.select_for_update().filter(pk=self.invoice_item_id)
            return super().delete(*args, **kwargs)


class VoteHead(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Vote Head"
        verbose_name_plural = "Vote Heads"

    def __str__(self):
        return self.name


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Expense Category"
        verbose_name_plural = "Expense Categories"

    def __str__(self):
        return self.name


class Income(models.Model):
    date = models.DateField()
    vote_head = models.ForeignKey("VoteHead", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=255, blank=True, help_text="e.g. donor name, department, buyer")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.vote_head} - {self.amount} on {self.date}"


class Expense(models.Model):
    date = models.DateField()
    category = models.ForeignKey("ExpenseCategory", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    payee = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.category} - {self.amount} on {self.date}"

