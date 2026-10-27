import logging
from io import BytesIO

import requests
from django.conf import settings
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView, View
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from apps.students.models import Student

from .forms import InvoiceItemFormset, InvoiceReceiptFormSet, Invoices
from .forms import (
    InvoiceItemFormset,
    InvoiceReceiptFormSet,
    Invoices,
    VoteHeadForm,
    ExpenseCategoryForm,
    IncomeForm,
    ExpenseForm,
    AllocationPerItemForm,
)
from .models import Invoice, InvoiceItem, Receipt, VoteHead, ExpenseCategory, Income, Expense
from .models import PaymentAllocation
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError

logger = logging.getLogger(__name__)


class InvoiceListView(LoginRequiredMixin, ListView):
    model = Invoice


class InvoiceStudentSelectionMixin:
    student_search_limit = 10

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        student_id = self.request.POST.get("student") if self.request.method == "POST" else None
        if student_id is None and getattr(self, "object", None):
            student_id = self.object.student_id
        students = Student.objects.none()
        if student_id and str(student_id).isdigit():
            students = Student.objects.filter(pk=student_id)
        form.fields["student"].queryset = students
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        selected_student = form.fields["student"].queryset.first()
        context["selected_student_label"] = str(selected_student) if selected_student else ""
        context["student_search_url"] = reverse_lazy("student-search")
        return context


class InvoiceStudentSearchView(LoginRequiredMixin, View):
    result_limit = 10

    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "").strip()
        if not query:
            return JsonResponse({"results": []})

        students = Student.objects.select_related("current_class").only(
            "pk",
            "surname",
            "firstname",
            "other_name",
            "registration_number",
            "current_class__id",
            "current_class__name",
        )
        for term in query.split():
            students = students.filter(
                Q(surname__icontains=term)
                | Q(firstname__icontains=term)
                | Q(other_name__icontains=term)
                | Q(registration_number__icontains=term)
            )

        results = [
            {
                "id": student.pk,
                "name": " ".join(
                    part
                    for part in (
                        student.surname,
                        student.firstname,
                        student.other_name,
                    )
                    if part
                ),
                "registration_number": student.registration_number,
                "class_name": str(student.current_class) if student.current_class else "",
            }
            for student in students.order_by("surname", "firstname", "other_name")[: self.result_limit]
        ]
        return JsonResponse({"results": results})


class InvoiceCreateView(InvoiceStudentSelectionMixin, LoginRequiredMixin, CreateView):
    model = Invoice
    fields = "__all__"
    success_url = "/finance/list"

    def get_context_data(self, **kwargs):
        context = super(InvoiceCreateView, self).get_context_data(**kwargs)
        if self.request.POST:
            context["items"] = InvoiceItemFormset(
                self.request.POST, prefix="invoiceitem_set"
            )
        else:
            context["items"] = InvoiceItemFormset(prefix="invoiceitem_set")
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        formset = context["items"]
        if not form.is_valid() or not formset.is_valid():
            return self.form_invalid(form)

        with transaction.atomic():
            self.object = form.save()
            formset.instance = self.object
            formset.save()

        return redirect(self.get_success_url())


class InvoiceDetailView(LoginRequiredMixin, DetailView):
    model = Invoice
    fields = "__all__"

    def get_context_data(self, **kwargs):
        context = super(InvoiceDetailView, self).get_context_data(**kwargs)
        context["receipts"] = Receipt.objects.filter(invoice=self.object)
        context["items"] = InvoiceItem.objects.filter(invoice=self.object)
        return context


class InvoiceUpdateView(InvoiceStudentSelectionMixin, LoginRequiredMixin, UpdateView):
    model = Invoice
    fields = ["student", "session", "term", "class_for", "balance_from_previous_term"]

    def get_context_data(self, **kwargs):
        context = super(InvoiceUpdateView, self).get_context_data(**kwargs)
        if self.request.POST:
            context["receipts"] = InvoiceReceiptFormSet(
                self.request.POST, instance=self.object
            )
            context["items"] = InvoiceItemFormset(
                self.request.POST, instance=self.object
            )
        else:
            context["receipts"] = InvoiceReceiptFormSet(instance=self.object)
            context["items"] = InvoiceItemFormset(instance=self.object)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        formset = context["receipts"]
        itemsformset = context["items"]
        if form.is_valid() and formset.is_valid() and itemsformset.is_valid():
            form.save()
            formset.save()
            itemsformset.save()
        return super().form_valid(form)


class InvoiceDeleteView(LoginRequiredMixin, DeleteView):
    model = Invoice
    success_url = reverse_lazy("invoice-list")

    @transaction.atomic
    def form_valid(self, form):
        invoice = self.object
        related_allocations = PaymentAllocation.objects.filter(
            Q(invoice_item__invoice=invoice) | Q(receipt__invoice=invoice)
        )
        cross_invoice_allocations = related_allocations.exclude(
            invoice_item__invoice=invoice,
            receipt__invoice=invoice,
        )
        if cross_invoice_allocations.exists():
            raise ProtectedError(
                "Cannot delete an invoice with allocations crossing invoice boundaries.",
                list(cross_invoice_allocations),
            )

        related_allocations.delete()
        invoice.delete()
        return redirect(self.get_success_url())


class ReceiptCreateView(LoginRequiredMixin, CreateView):
    model = Receipt
    fields = ["amount_paid", "date_paid", "reference_number", "payment_method", "comment"]
    success_url = reverse_lazy("invoice-list")

    def form_valid(self, form):
        obj = form.save(commit=False)
        invoice = Invoice.objects.get(pk=self.request.GET["invoice"])
        obj.invoice = invoice
        obj.save()

        webhook_url = getattr(settings, "COLLEGE_SYSTEM_WEBHOOK_URL", None)
        if webhook_url:
            payload = {
                "registration_number": invoice.student.registration_number,
                "invoice_id": invoice.pk,
                "amount_paid": obj.amount_paid,
                "invoice_status": invoice.status,
                "balance": invoice.balance(),
                "receipt_date": obj.date_paid.isoformat() if hasattr(obj.date_paid, "isoformat") else str(obj.date_paid),
            }
            headers = {}
            api_key = getattr(settings, "EXTERNAL_API_KEY", None)
            if api_key:
                headers["X-API-KEY"] = api_key

            try:
                requests.post(
                    webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=5,
                )
            except Exception as exc:
                logger.exception(
                    "Receipt webhook delivery failed for invoice=%s receipt=%s",
                    invoice.pk,
                    obj.pk,
                )

        return redirect("receipt-print", pk=obj.pk)

    def get_context_data(self, **kwargs):
        context = super(ReceiptCreateView, self).get_context_data(**kwargs)
        invoice = Invoice.objects.get(pk=self.request.GET["invoice"])
        context["invoice"] = invoice
        return context


@login_required
def receipt_print(request, pk):
    # Load receipt with related invoice/student and allocations to avoid N+1
    receipt = (
        Receipt.objects.select_related(
            'invoice__student', 'invoice__session', 'invoice__term', 'invoice__class_for'
        )
        .prefetch_related('allocations__invoice_item__vote_head')
        .get(pk=pk)
    )
    invoice = receipt.invoice
    context = {
        "receipt": receipt,
        "invoice": invoice,
    }
    return render(request, "finance/receipt_print.html", context)


@login_required
def receipt_pdf(request, pk):
    receipt = Receipt.objects.get(pk=pk)
    invoice = receipt.invoice

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle(f"Receipt {receipt.pk}")

    y = 760
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Got Matar Institute of Technology")
    y -= 24
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, y, "Student Receipt")
    y -= 36

    pdf.drawString(50, y, f"Receipt Number: {receipt.pk}")
    y -= 18
    pdf.drawString(50, y, f"Date Paid: {receipt.date_paid}")
    y -= 18
    pdf.drawString(50, y, f"Amount Paid: {receipt.amount_paid}")
    y -= 18
    pdf.drawString(50, y, f"Comment: {receipt.comment}")
    y -= 36

    pdf.drawString(50, y, f"Student: {invoice.student.surname} {invoice.student.firstname} {invoice.student.other_name}")
    y -= 18
    pdf.drawString(50, y, f"Registration Number: {invoice.student.registration_number}")
    y -= 18
    pdf.drawString(50, y, f"Session: {invoice.session}")
    y -= 18
    pdf.drawString(50, y, f"Term: {invoice.term}")
    y -= 18
    pdf.drawString(50, y, f"Class: {invoice.class_for}")
    y -= 36

    pdf.drawString(50, y, f"Invoice Total: {invoice.total_amount_payable()}")
    y -= 18
    pdf.drawString(50, y, f"Total Paid: {invoice.total_amount_paid()}")
    y -= 18
    pdf.drawString(50, y, f"Balance Remaining After This Payment: {invoice.balance()}")

    pdf.showPage()
    pdf.save()

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename=receipt_{receipt.pk}.pdf"
    buffer.close()
    return response


def expensecategory_report_pdf(request):
    category_id = request.GET.get("category_id")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    cat = ExpenseCategory.objects.filter(pk=category_id).first() if category_id else None
    expenses = Expense.objects.none()
    if cat:
        expenses = Expense.objects.filter(category=cat)

    from django.utils.dateparse import parse_date
    if date_from:
        df = parse_date(date_from)
        expenses = expenses.filter(date__gte=df)
    if date_to:
        dt = parse_date(date_to)
        expenses = expenses.filter(date__lte=dt)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle(f"Expense Report {cat}")
    y = 760
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Got Matar Institute of Technology")
    y -= 20
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, f"Expense Report for: {cat.name if cat else 'All'}")
    y -= 18
    pdf.setFont("Helvetica", 10)
    if date_from or date_to:
        pdf.drawString(50, y, f"Period: {date_from or '-'} to {date_to or '-'}")
        y -= 16

    pdf.drawString(50, y, "Expenses:")
    y -= 16
    pdf.setFont("Helvetica", 9)
    for e in expenses:
        line = f"{e.date}  {e.amount}  {e.description}  {e.payee}"
        pdf.drawString(50, y, line)
        y -= 12
        if y < 72:
            pdf.showPage()
            y = 760

    y -= 8
    pdf.setFont("Helvetica-Bold", 10)
    expense_total = expenses.aggregate(total=Sum("amount"))["total"] or 0
    pdf.drawString(50, y, f"Expense Total: {expense_total}")

    pdf.showPage()
    pdf.save()
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename=expense_report_{category_id}.pdf"
    buffer.close()
    return response


def summary_report_pdf(request):
    # Build summary using the shared helper so HTML/PDF agree
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    df = parse_date(date_from) if date_from else None
    dt = parse_date(date_to) if date_to else None
    summary = compute_summary(df, dt)

    income_by_vh = summary['income_by_vh']
    fee_receipts = summary['fee_allocations']
    unallocated_rows = summary['unallocated_rows']
    alloc_total = summary['alloc_total']
    unalloc_total = summary['unalloc_total']
    expense_by_cat = expenses_by_category(df, dt)
    total_income = summary['total_income']
    total_fee_receipts = summary['total_fee_receipts']
    total_expenses = summary['total_expenses']
    net = summary['net']

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle("Summary Report")
    y = 760
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Got Matar Institute of Technology")
    y -= 20
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, "Combined Summary Report")
    y -= 18

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, y, "Income by Vote Head")
    y -= 14
    pdf.setFont("Helvetica", 9)
    for row in income_by_vh:
        pdf.drawString(50, y, f"{row.get('vote_head__name')}: {row.get('total')}")
        y -= 12
        if y < 72:
            pdf.showPage(); y = 760

    y -= 8
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, y, "Fee Allocations (by Vote Head)")
    y -= 12
    pdf.setFont("Helvetica", 9)
    for row in fee_receipts:
        pdf.drawString(50, y, f"{row.get('vote_head__name')}: {row.get('total')}")
        y -= 12
        if y < 72:
            pdf.showPage(); y = 760

    y -= 8
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, y, "Unallocated Fee Receipts")
    y -= 12
    pdf.setFont("Helvetica", 9)
    for row in unallocated_rows:
        receipt = row['receipt']
        pdf.drawString(
            50,
            y,
            f"Receipt {receipt.pk}  {receipt.date_paid}  {row['unallocated']}  Invoice {receipt.invoice_id}",
        )
        y -= 12
        if y < 72:
            pdf.showPage(); y = 760

    y -= 8
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, y, "Expenses by Category")
    y -= 12
    pdf.setFont("Helvetica", 9)
    for row in expense_by_cat:
        pdf.drawString(50, y, f"{row.get('category__name')}: {row.get('total')}")
        y -= 12
        if y < 72:
            pdf.showPage(); y = 760

    y -= 12
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, y, f"Total Income: {total_income}")
    y -= 12
    pdf.drawString(50, y, f"Total Allocated Fee Receipts: {alloc_total}")
    y -= 12
    pdf.drawString(50, y, f"Total Unallocated Fee Receipts: {unalloc_total}")
    y -= 12
    pdf.drawString(50, y, f"Total Fee Receipts: {total_fee_receipts}")
    y -= 12
    pdf.drawString(50, y, f"Total Expenses: {total_expenses}")
    y -= 12
    pdf.drawString(50, y, f"Net: {net}")

    pdf.showPage()
    pdf.save()
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = "attachment; filename=summary_report.pdf"
    buffer.close()
    return response


class ReceiptUpdateView(LoginRequiredMixin, UpdateView):
    model = Receipt
    fields = ["amount_paid", "date_paid", "reference_number", "payment_method", "comment"]
    success_url = reverse_lazy("invoice-list")

    def form_valid(self, form):
        # Prevent reducing amount_paid below already allocated total
        obj = form.save(commit=False)
        allocated = obj.allocations.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        if Decimal(obj.amount_paid) < Decimal(allocated):
            form.add_error('amount_paid', f'Cannot reduce receipt amount below already allocated total ({allocated}).')
            return self.form_invalid(form)
        return super().form_valid(form)


class ReceiptDeleteView(LoginRequiredMixin, DeleteView):
    model = Receipt
    success_url = reverse_lazy("invoice-list")


@login_required
def receipt_allocate(request, pk):
    receipt = Receipt.objects.select_related('invoice').get(pk=pk)
    invoice = receipt.invoice
    items = InvoiceItem.objects.filter(invoice=invoice)

    # Map existing allocations by invoice_item_id (sum if multiple)
    existing_allocs = PaymentAllocation.objects.filter(receipt=receipt)
    alloc_map = {}
    for a in existing_allocs:
        iid = a.invoice_item_id
        alloc_map[iid] = alloc_map.get(iid, Decimal('0.00')) + a.amount

    if request.method == 'POST':
        forms = []
        total_submitted = Decimal('0.00')
        for item in items:
            key = f'alloc_{item.id}'
            val = request.POST.get(key, '').strip()
            if val == '':
                amt = Decimal('0.00')
            else:
                try:
                    amt = Decimal(val)
                except Exception:
                    messages.error(request, f'Invalid amount for item {item.description}')
                    return redirect(request.path)
            forms.append((item, amt))
            total_submitted += amt

        # Perform transactional updates: update/create/delete allocations to match submitted amounts
        try:
            with transaction.atomic():
                # First check overall limit
                if total_submitted > receipt.amount_paid:
                    raise ValidationError('Total allocations exceed receipt amount')

                # For each item, sync allocation
                for item, amt in forms:
                    existing = PaymentAllocation.objects.filter(receipt=receipt, invoice_item=item).first()
                    if amt == Decimal('0.00'):
                        if existing:
                            existing.delete()
                    else:
                        if existing:
                            # Do not overwrite original created_by when updating allocation
                            existing.amount = amt
                            existing.save()
                        else:
                            pa = PaymentAllocation(receipt=receipt, invoice_item=item, amount=amt, created_by=(request.user if request.user.is_authenticated else None))
                            pa.save()
                messages.success(request, 'Allocations saved')
                return redirect('receipt-print', pk=receipt.pk)
        except ValidationError as ve:
            messages.error(request, str(ve))
        except Exception as exc:
            messages.error(request, f'Allocation failed: {exc}')

    # Prepare forms for GET
    allocation_rows = []
    total_allocated = Decimal('0.00')
    for item in items:
        allocated = alloc_map.get(item.id, Decimal('0.00'))
        total_allocated += allocated
        allocation_rows.append(
            {
                'item': item,
                'vote_head': item.vote_head,
                'item_amount': item.amount,
                'allocated': allocated,
                'outstanding': item.outstanding_amount(),
            }
        )

    context = {
        'receipt': receipt,
        'invoice': invoice,
        'allocation_rows': allocation_rows,
        'total_allocated': total_allocated,
        'unallocated': Decimal(receipt.amount_paid) - total_allocated,
    }
    return render(request, 'finance/receipt_allocate.html', context)


class PaymentAllocationUpdateView(LoginRequiredMixin, UpdateView):
    model = PaymentAllocation
    fields = ['amount', 'note']
    template_name = 'finance/paymentallocation_form.html'

    def form_valid(self, form):
        obj = form.save(commit=False)
        # Do NOT overwrite original created_by on edit to preserve audit trail
        obj.save()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('receipt-print', kwargs={'pk': self.object.receipt.pk})


class PaymentAllocationDeleteView(LoginRequiredMixin, DeleteView):
    model = PaymentAllocation
    template_name = 'corecode/core_confirm_delete.html'
    success_url = reverse_lazy('invoice-list')


@login_required
def bulk_invoice(request):
    return render(request, "finance/bulk_invoice.html")


class VoteHeadListView(LoginRequiredMixin, ListView):
    model = VoteHead
    template_name = "finance/votehead_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = VoteHeadForm()
        return context


class VoteHeadCreateView(LoginRequiredMixin, CreateView):
    model = VoteHead
    form_class = VoteHeadForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("votehead-list")


class VoteHeadUpdateView(LoginRequiredMixin, UpdateView):
    model = VoteHead
    form_class = VoteHeadForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("votehead-list")


class VoteHeadDeleteView(LoginRequiredMixin, DeleteView):
    model = VoteHead
    template_name = "corecode/core_confirm_delete.html"
    success_url = reverse_lazy("votehead-list")


class ExpenseCategoryListView(LoginRequiredMixin, ListView):
    model = ExpenseCategory
    template_name = "finance/expensecategory_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = ExpenseCategoryForm()
        return context


class ExpenseCategoryCreateView(LoginRequiredMixin, CreateView):
    model = ExpenseCategory
    form_class = ExpenseCategoryForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("expensecategory-list")


class ExpenseCategoryUpdateView(LoginRequiredMixin, UpdateView):
    model = ExpenseCategory
    form_class = ExpenseCategoryForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("expensecategory-list")


class ExpenseCategoryDeleteView(LoginRequiredMixin, DeleteView):
    model = ExpenseCategory
    template_name = "corecode/core_confirm_delete.html"
    success_url = reverse_lazy("expensecategory-list")


class IncomeCreateView(LoginRequiredMixin, CreateView):
    model = Income
    form_class = IncomeForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("income-list")


class IncomeUpdateView(LoginRequiredMixin, UpdateView):
    model = Income
    form_class = IncomeForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("income-list")

    def form_valid(self, form):
        messages.success(self.request, "Income record updated successfully.")
        return super().form_valid(form)


class IncomeDeleteView(LoginRequiredMixin, DeleteView):
    model = Income
    template_name = "finance/record_confirm_delete.html"
    success_url = reverse_lazy("income-list")
    http_method_names = ["get", "post", "head", "options"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = self.success_url
        return context

    @transaction.atomic
    def form_valid(self, form):
        messages.success(self.request, "Income record deleted successfully.")
        return super().form_valid(form)


class IncomeListView(LoginRequiredMixin, ListView):
    model = Income
    template_name = "finance/income_list.html"


class ExpenseCreateView(LoginRequiredMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("expense-list")


class ExpenseUpdateView(LoginRequiredMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "corecode/mgt_form.html"
    success_url = reverse_lazy("expense-list")

    def form_valid(self, form):
        messages.success(self.request, "Expense record updated successfully.")
        return super().form_valid(form)


class ExpenseDeleteView(LoginRequiredMixin, DeleteView):
    model = Expense
    template_name = "finance/record_confirm_delete.html"
    success_url = reverse_lazy("expense-list")
    http_method_names = ["get", "post", "head", "options"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = self.success_url
        return context

    @transaction.atomic
    def form_valid(self, form):
        messages.success(self.request, "Expense record deleted successfully.")
        return super().form_valid(form)


class ExpenseListView(LoginRequiredMixin, ListView):
    model = Expense
    template_name = "finance/expense_list.html"


from django.db.models import Sum, Q
from django.utils.dateparse import parse_date
from .reports import (
    allocations_by_vote_head,
    total_allocations,
    unallocated_fee_receipts,
    income_by_vote_head,
    expenses_by_category,
    compute_summary,
)


def votehead_report(request):
    vote_head_id = request.GET.get("vote_head_id")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    vh = VoteHead.objects.filter(pk=vote_head_id).first() if vote_head_id else None

    # Income entries (non-fee other income attached to this vote head)
    incomes = Income.objects.filter(vote_head=vh) if vh else Income.objects.none()

    # Date parsing
    df = parse_date(date_from) if date_from else None
    dt = parse_date(date_to) if date_to else None
    if df:
        incomes = incomes.filter(date__gte=df)
    if dt:
        incomes = incomes.filter(date__lte=dt)

    # Allocations (fee collections) for this vote head
    allocations = PaymentAllocation.objects.filter(invoice_item__vote_head=vh).select_related('receipt', 'invoice_item') if vh else PaymentAllocation.objects.none()
    if df:
        allocations = allocations.filter(receipt__date_paid__gte=df)
    if dt:
        allocations = allocations.filter(receipt__date_paid__lte=dt)

    allocation_total = allocations.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Receipts touching this vote head's invoice items (used to show unallocated amounts separately)
    receipts = Receipt.objects.filter(invoice__invoiceitem__vote_head=vh).distinct() if vh else Receipt.objects.none()
    if df:
        receipts = receipts.filter(date_paid__gte=df)
    if dt:
        receipts = receipts.filter(date_paid__lte=dt)

    # Unallocated per receipt (for this vote head's receipts)
    unallocated_list = []
    total_unallocated = Decimal('0.00')
    for r in receipts:
        ua = Decimal(r.unallocated_amount())
        unallocated_list.append({'receipt': r, 'unallocated': ua})
        total_unallocated += ua

    income_total = incomes.aggregate(total=Sum("amount"))["total"] or Decimal('0.00')
    receipt_total = receipts.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

    context = {
        "vote_head": vh,
        "incomes": incomes,
        "allocations": allocations,
        "allocation_total": allocation_total,
        "receipts": receipts,
        "unallocated_list": unallocated_list,
        "total_unallocated": total_unallocated,
        "receipt_total": receipt_total,
        "income_total": income_total,
    }
    return render(request, "finance/votehead_report.html", context)


def expensecategory_report(request):
    category_id = request.GET.get("category_id")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    expenses = Expense.objects.none()
    if category_id:
        cat = ExpenseCategory.objects.get(pk=category_id)
        expenses = Expense.objects.filter(category=cat)
    if date_from:
        df = parse_date(date_from)
        expenses = expenses.filter(date__gte=df)
    if date_to:
        dt = parse_date(date_to)
        expenses = expenses.filter(date__lte=dt)

    expense_total = expenses.aggregate(total=Sum("amount"))["total"] or 0
    context = {"category": ExpenseCategory.objects.filter(pk=category_id).first(), "expenses": expenses, "expense_total": expense_total}
    return render(request, "finance/expensecategory_report.html", context)


def summary_report(request):
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    df = parse_date(date_from) if date_from else None
    dt = parse_date(date_to) if date_to else None

    summary = compute_summary(df, dt)

    # adapt keys expected by templates
    income_by_vh = summary['income_by_vh']
    fee_receipts = summary['fee_allocations']
    expense_by_cat = expenses_by_category(df, dt)
    total_income = summary['total_income']
    total_fee_receipts = summary['total_fee_receipts']
    total_expenses = summary['total_expenses']
    net = summary['net']

    context = {
        "income_by_vh": income_by_vh,
        "fee_receipts": fee_receipts,
        "unallocated_rows": summary["unallocated_rows"],
        "alloc_total": summary["alloc_total"],
        "unalloc_total": summary["unalloc_total"],
        "expense_by_cat": expense_by_cat,
        "total_income": total_income,
        "total_fee_receipts": total_fee_receipts,
        "total_expenses": total_expenses,
        "net": net,
    }
    return render(request, "finance/summary_report.html", context)


def votehead_report_pdf(request):
    # Use the same filtering logic as votehead_report to build context
    vote_head_id = request.GET.get("vote_head_id")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    vh = VoteHead.objects.filter(pk=vote_head_id).first() if vote_head_id else None
    from django.utils.dateparse import parse_date
    df = parse_date(date_from) if date_from else None
    dt = parse_date(date_to) if date_to else None

    incomes = Income.objects.filter(vote_head=vh) if vh else Income.objects.none()
    if df:
        incomes = incomes.filter(date__gte=df)
    if dt:
        incomes = incomes.filter(date__lte=dt)

    allocations = PaymentAllocation.objects.filter(invoice_item__vote_head=vh).select_related('receipt', 'invoice_item') if vh else PaymentAllocation.objects.none()
    if df:
        allocations = allocations.filter(receipt__date_paid__gte=df)
    if dt:
        allocations = allocations.filter(receipt__date_paid__lte=dt)

    receipts = Receipt.objects.filter(invoice__invoiceitem__vote_head=vh).distinct() if vh else Receipt.objects.none()
    if df:
        receipts = receipts.filter(date_paid__gte=df)
    if dt:
        receipts = receipts.filter(date_paid__lte=dt)

    # generate PDF using the same letterhead style as receipts
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle(f"Report {vh}")

    y = 760
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Got Matar Institute of Technology")
    y -= 20
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, f"Report for: {vh.name if vh else 'All'}")
    y -= 18
    pdf.setFont("Helvetica", 10)
    if date_from or date_to:
        pdf.drawString(50, y, f"Period: {date_from or '-'} to {date_to or '-'}")
        y -= 16

    pdf.drawString(50, y, "Income entries:")
    y -= 16
    pdf.setFont("Helvetica", 9)
    for inc in incomes:
        line = f"{inc.date}  {inc.amount}  {inc.description}"
        pdf.drawString(50, y, line)
        y -= 12
        if y < 72:
            pdf.showPage()
            y = 760

    y -= 6
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, "Fee Receipts:")
    y -= 14
    pdf.setFont("Helvetica", 9)
    # Show allocations for this vote head
    pdf.drawString(50, y, "Allocations:")
    y -= 12
    for a in allocations:
        line = f"{a.receipt.date_paid}  {a.amount}  Invoice {a.receipt.invoice_id}"
        pdf.drawString(50, y, line)
        y -= 12
        if y < 72:
            pdf.showPage(); y = 760

    y -= 6
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, "Receipts touching this vote head (unallocated shown):")
    y -= 12
    pdf.setFont("Helvetica", 9)
    for r in receipts:
        line = f"{r.date_paid}  {r.amount_paid}  Invoice {r.invoice_id}  Unallocated: {r.unallocated_amount()}"
        pdf.drawString(50, y, line)
        y -= 12
        if y < 72:
            pdf.showPage(); y = 760

    # totals
    y -= 8
    pdf.setFont("Helvetica-Bold", 10)
    income_total = incomes.aggregate(total=Sum("amount"))["total"] or 0
    allocation_total = allocations.aggregate(total=Sum('amount'))['total'] or 0
    receipt_total = receipts.aggregate(total=Sum("amount_paid"))["total"] or 0
    pdf.drawString(50, y, f"Income Total: {income_total}")
    y -= 14
    pdf.drawString(50, y, f"Fee Allocated Total: {allocation_total}")
    y -= 14
    pdf.drawString(50, y, f"Original Receipt Amount (Context Only): {receipt_total}")
    y -= 14
    pdf.setFont("Helvetica", 8)
    pdf.drawString(
        50,
        y,
        "This is the original receipt amount. It is shown for context only and is not the amount attributed to this VoteHead.",
    )

    pdf.showPage()
    pdf.save()
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename=votehead_report_{vote_head_id}.pdf"
    buffer.close()
    return response
