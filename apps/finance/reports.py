from decimal import Decimal
from django.db.models import Sum

from .models import PaymentAllocation, Receipt, Income, Expense


def allocations_by_vote_head(date_from=None, date_to=None):
    qs = PaymentAllocation.objects.filter(
        invoice_item__vote_head__isnull=False
    ).select_related('invoice_item__vote_head', 'receipt')
    if date_from:
        qs = qs.filter(receipt__date_paid__gte=date_from)
    if date_to:
        qs = qs.filter(receipt__date_paid__lte=date_to)

    rows = qs.values('invoice_item__vote_head__name').annotate(total=Sum('amount'))
    # Normalize keys to match templates ('vote_head__name')
    result = []
    for r in rows:
        result.append({'vote_head__name': r.get('invoice_item__vote_head__name'), 'total': r.get('total') or Decimal('0.00')})
    return result


def total_allocations(date_from=None, date_to=None):
    qs = PaymentAllocation.objects.filter(
        invoice_item__vote_head__isnull=False
    ).select_related('receipt')
    if date_from:
        qs = qs.filter(receipt__date_paid__gte=date_from)
    if date_to:
        qs = qs.filter(receipt__date_paid__lte=date_to)
    return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')


def unallocated_fee_receipts(date_from=None, date_to=None):
    receipts = Receipt.objects.all().select_related('invoice')
    if date_from:
        receipts = receipts.filter(date_paid__gte=date_from)
    if date_to:
        receipts = receipts.filter(date_paid__lte=date_to)

    rows = []
    total_unallocated = Decimal('0.00')
    for r in receipts:
        allocated = PaymentAllocation.objects.filter(
            receipt=r,
            invoice_item__vote_head__isnull=False,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        ua = max(Decimal('0.00'), Decimal(r.amount_paid) - Decimal(allocated))
        if ua > Decimal('0.00'):
            rows.append({'receipt': r, 'unallocated': ua})
        total_unallocated += ua
    return rows, total_unallocated


def income_by_vote_head(date_from=None, date_to=None):
    qs = Income.objects.all()
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return qs.values('vote_head__name').annotate(total=Sum('amount'))


def expenses_by_category(date_from=None, date_to=None):
    qs = Expense.objects.all()
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return qs.values('category__name').annotate(total=Sum('amount'))


def compute_summary(date_from=None, date_to=None):
    income_rows = income_by_vote_head(date_from, date_to)
    alloc_rows = allocations_by_vote_head(date_from, date_to)
    alloc_total = total_allocations(date_from, date_to)
    unalloc_rows, unalloc_total = unallocated_fee_receipts(date_from, date_to)
    total_fee_receipts = alloc_total + unalloc_total
    total_income = sum([r['total'] or Decimal('0.00') for r in income_rows]) if income_rows is not None else Decimal('0.00')
    expenses = Expense.objects.all()
    if date_from:
        expenses = expenses.filter(date__gte=date_from)
    if date_to:
        expenses = expenses.filter(date__lte=date_to)
    total_expenses = expenses.aggregate(total=Sum('amount'))['total']
    total_expenses = total_expenses or Decimal('0.00')
    net = (Decimal(total_income) + Decimal(total_fee_receipts)) - Decimal(total_expenses)
    return {
        'income_by_vh': income_rows,
        'fee_allocations': alloc_rows,
        'alloc_total': alloc_total,
        'unallocated_rows': unalloc_rows,
        'unalloc_total': unalloc_total,
        'total_fee_receipts': total_fee_receipts,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'net': net,
    }
