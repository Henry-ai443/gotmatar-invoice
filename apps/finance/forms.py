from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory, modelformset_factory

from .models import (
    Invoice,
    InvoiceItem,
    Receipt,
    VoteHead,
    ExpenseCategory,
    Income,
    Expense,
)


class AllocationPerItemForm(forms.Form):
    invoice_item_id = forms.IntegerField(widget=forms.HiddenInput)
    allocation = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=12)


class VoteHeadForm(forms.ModelForm):
    class Meta:
        model = VoteHead
        fields = ["name", "parent", "is_active"]


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ["name", "parent", "is_active"]


class IncomeForm(forms.ModelForm):
    class Meta:
        model = Income
        fields = ["date", "vote_head", "amount", "description", "source"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = VoteHead.objects.filter(is_active=True)
        if self.instance and self.instance.vote_head_id:
            queryset = VoteHead.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.vote_head_id)
            )
        self.fields["vote_head"].queryset = queryset


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["date", "category", "amount", "description", "payee"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = ExpenseCategory.objects.filter(is_active=True)
        if self.instance and self.instance.category_id:
            queryset = ExpenseCategory.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.category_id)
            )
        self.fields["category"].queryset = queryset


class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ["description", "amount", "vote_head"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vote_head"].required = True
        self.fields["vote_head"].error_messages["required"] = "Vote Head is required."
        queryset = VoteHead.objects.filter(is_active=True)
        if self.instance and self.instance.vote_head_id:
            queryset = VoteHead.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.vote_head_id)
            )
        self.fields["vote_head"].queryset = queryset


InvoiceItemFormset = inlineformset_factory(
    Invoice,
    InvoiceItem,
    form=InvoiceItemForm,
    extra=1,
    can_delete=True,
)

InvoiceReceiptFormSet = inlineformset_factory(
    Invoice,
    Receipt,
    fields=("amount_paid", "date_paid", "comment", "reference_number", "payment_method"),
    extra=0,
    can_delete=True,
)

Invoices = modelformset_factory(Invoice, exclude=(), extra=4)
