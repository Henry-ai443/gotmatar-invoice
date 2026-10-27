import os
import io
import re
import sys
import json
from django.test import Client
from django.db.models import Sum
from pdfminer.high_level import extract_text

# ensure project root is on sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'school_app.settings')
import django
django.setup()

from apps.finance.models import VoteHead, ExpenseCategory, Income, Expense, Receipt, InvoiceItem

amount_re = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b")

def parse_number(s):
    # normalize number-like strings: remove commas and surrounding whitespace
    if s is None:
        return None
    s = re.sub(r"\s+", "", str(s))
    s = s.replace(',', '')
    try:
        if '.' in s:
            return float(s)
        return int(s)
    except:
        return None

def get_pdf_text_pdfminer(content_bytes):
    f = io.BytesIO(content_bytes)
    # pdfminer.extract_text can read from a file-like object
    try:
        text = extract_text(f)
    except Exception:
        # fallback to empty string on error
        text = ''
    return text or ''

# Mirror the checks from the original script but using pdfminer extraction

def run_checks():
    c = Client()
    assert c.login(username='admin@admin.com', password='changeme123')

    reports = []

    # Fees
    vh = VoteHead.objects.get(name='Fees')
    orm_receipts = list(Receipt.objects.filter(invoice__invoiceitem__vote_head=vh).values('id','invoice_id','amount_paid','date_paid'))
    orm_receipt_total = sum(r['amount_paid'] for r in orm_receipts) if orm_receipts else 0
    resp = c.get(f'/finance/reports/votehead/pdf/?vote_head_id={vh.id}&date_from=2026-08-01&date_to=2026-08-31')
    text = get_pdf_text_pdfminer(resp.content)
    lines = [L.strip() for L in text.splitlines() if L.strip()]
    extracted_receipts = []
    for r in orm_receipts:
        matched = [L for L in lines if str(r['invoice_id']) in L or (str(r['amount_paid']) in L.replace(',',''))]
        extracted_receipts.append({'id': r['id'], 'invoice_id': r['invoice_id'], 'amount_paid': r['amount_paid'], 'matched_lines': matched})
    pdf_numbers = [parse_number(m) for L in lines for m in amount_re.findall(L)]
    pdf_total = next((n for n in set(x for x in pdf_numbers if x is not None) if n == orm_receipt_total), None)
    reports.append({'name':'Fees','orm_transactions': orm_receipts, 'orm_total': orm_receipt_total, 'pdf_lines': lines, 'pdf_transactions': extracted_receipts, 'pdf_numbers': pdf_numbers, 'pdf_total': pdf_total})

    # GMCDG
    vh = VoteHead.objects.get(name='GMCDG')
    orm_incomes = list(Income.objects.filter(vote_head=vh).values('id','date','amount','description'))
    orm_income_total = sum(i['amount'] for i in orm_incomes) if orm_incomes else 0
    resp = c.get(f'/finance/reports/votehead/pdf/?vote_head_id={vh.id}&date_from=2026-08-01&date_to=2026-08-31')
    text = get_pdf_text_pdfminer(resp.content)
    lines = [L.strip() for L in text.splitlines() if L.strip()]
    extracted_incomes = []
    for inc in orm_incomes:
        matched = [L for L in lines if str(inc['amount']) in L.replace(',','') or (inc.get('description') and inc['description'] in L)]
        extracted_incomes.append({'id': inc['id'], 'amount': inc['amount'], 'description': inc['description'], 'matched_lines': matched})
    pdf_numbers = [parse_number(m) for L in lines for m in amount_re.findall(L)]
    pdf_total = next((n for n in set(x for x in pdf_numbers if x is not None) if n == orm_income_total), None)
    reports.append({'name':'GMCDG','orm_transactions': orm_incomes, 'orm_total': orm_income_total, 'pdf_lines': lines, 'pdf_transactions': extracted_incomes, 'pdf_numbers': pdf_numbers, 'pdf_total': pdf_total})

    # Sales
    vh = VoteHead.objects.get(name='Sales of Goods')
    orm_incomes = list(Income.objects.filter(vote_head=vh).values('id','date','amount','description'))
    orm_income_total = sum(i['amount'] for i in orm_incomes) if orm_incomes else 0
    resp = c.get(f'/finance/reports/votehead/pdf/?vote_head_id={vh.id}&date_from=2026-08-01&date_to=2026-08-31')
    text = get_pdf_text_pdfminer(resp.content)
    lines = [L.strip() for L in text.splitlines() if L.strip()]
    extracted_incomes = []
    for inc in orm_incomes:
        matched = [L for L in lines if str(inc['amount']) in L.replace(',','') or (inc.get('description') and inc['description'] in L)]
        extracted_incomes.append({'id': inc['id'], 'amount': inc['amount'], 'description': inc['description'], 'matched_lines': matched})
    pdf_numbers = [parse_number(m) for L in lines for m in amount_re.findall(L)]
    pdf_total = next((n for n in set(x for x in pdf_numbers if x is not None) if n == orm_income_total), None)
    reports.append({'name':'Sales','orm_transactions': orm_incomes, 'orm_total': orm_income_total, 'pdf_lines': lines, 'pdf_transactions': extracted_incomes, 'pdf_numbers': pdf_numbers, 'pdf_total': pdf_total})

    # Utilities
    cat = ExpenseCategory.objects.get(name='Utilities')
    orm_expenses = list(Expense.objects.filter(category=cat).values('id','date','amount','description','payee'))
    orm_expense_total = sum(e['amount'] for e in orm_expenses) if orm_expenses else 0
    resp = c.get(f'/finance/reports/expensecategory/pdf/?category_id={cat.id}&date_from=2026-08-01&date_to=2026-08-31')
    text = get_pdf_text_pdfminer(resp.content)
    lines = [L.strip() for L in text.splitlines() if L.strip()]
    extracted_expenses = []
    for e in orm_expenses:
        matched = [L for L in lines if str(e['amount']) in L.replace(',','') or (e.get('description') and e['description'] in L) or (e.get('payee') and e['payee'] in L)]
        extracted_expenses.append({'id': e['id'], 'amount': e['amount'], 'description': e['description'], 'payee': e['payee'], 'matched_lines': matched})
    pdf_numbers = [parse_number(m) for L in lines for m in amount_re.findall(L)]
    pdf_total = next((n for n in set(x for x in pdf_numbers if x is not None) if n == orm_expense_total), None)
    reports.append({'name':'Utilities','orm_transactions': orm_expenses, 'orm_total': orm_expense_total, 'pdf_lines': lines, 'pdf_transactions': extracted_expenses, 'pdf_numbers': pdf_numbers, 'pdf_total': pdf_total})

    # Summary
    income_by_vh = list(Income.objects.values('vote_head__name').annotate(total=Sum('amount')))
    fee_receipts = list(InvoiceItem.objects.values('vote_head__name').annotate(total=Sum('invoice__receipt__amount_paid')))
    expense_by_cat = list(Expense.objects.values('category__name').annotate(total=Sum('amount')))
    total_income = Income.objects.aggregate(total=Sum('amount'))['total'] or 0
    total_fee_receipts = Receipt.objects.filter(invoice__invoiceitem__vote_head__isnull=False).distinct().aggregate(total=Sum('amount_paid'))['total'] or 0
    total_expenses = Expense.objects.aggregate(total=Sum('amount'))['total'] or 0
    net = total_income + total_fee_receipts - total_expenses
    resp = c.get('/finance/reports/summary/pdf/')
    text = get_pdf_text_pdfminer(resp.content)
    lines = [L.strip() for L in text.splitlines() if L.strip()]
    pdf_numbers = [parse_number(m) for L in lines for m in amount_re.findall(L)]
    summary_pdf = {'total_income': None, 'total_fee_receipts': None, 'total_expenses': None, 'net': None}
    for n in set(x for x in pdf_numbers if x is not None):
        if n == total_income:
            summary_pdf['total_income'] = n
        if n == total_fee_receipts:
            summary_pdf['total_fee_receipts'] = n
        if n == total_expenses:
            summary_pdf['total_expenses'] = n
        if n == net:
            summary_pdf['net'] = n
    reports.append({'name':'Summary','orm': {'income_by_vh': income_by_vh, 'fee_receipts': fee_receipts, 'expense_by_cat': expense_by_cat, 'total_income': total_income, 'total_fee_receipts': total_fee_receipts, 'total_expenses': total_expenses, 'net': net}, 'pdf_lines': lines, 'pdf_numbers': pdf_numbers, 'summary_pdf': summary_pdf})

    print(json.dumps(reports, default=str, indent=2))

if __name__ == '__main__':
    run_checks()
