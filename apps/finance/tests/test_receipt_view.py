from decimal import Decimal
import re
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.finance.models import (
    Invoice,
    InvoiceItem,
    Receipt,
    PaymentAllocation,
    VoteHead,
)
from apps.students.models import Student
from apps.corecode.models import AcademicSession, AcademicTerm, StudentClass


class ReceiptViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='tester', password='pwd')
        self.client.force_login(self.user)

        self.student = Student.objects.first() or Student.objects.create(surname='A', firstname='B', registration_number='R001')
        self.session = AcademicSession.objects.first() or AcademicSession.objects.create(name='S1')
        self.term = AcademicTerm.objects.first() or AcademicTerm.objects.create(name='T1')
        self.sclass = StudentClass.objects.first() or StudentClass.objects.create(name='C1')

        self.vh_tuition = VoteHead.objects.create(name='Tuition_v')
        self.vh_practical = VoteHead.objects.create(name='Practical_v')

    def _create_invoice(self, items):
        inv = Invoice.objects.create(student=self.student, session=self.session, term=self.term, class_for=self.sclass)
        created = []
        for desc, amt, vh in items:
            created.append(InvoiceItem.objects.create(invoice=inv, description=desc, amount=amt, vote_head=vh))
        return inv, created

    def assertContainsNumber(self, response, number):
        # Normalize by removing non-digit characters so formatted numbers (e.g. 5,000.00)
        # still match numeric values (e.g. 5000)
        resp_text = response.content.decode('utf-8')
        def norm(x):
            return re.sub(r"[^0-9]", "", str(x))
        self.assertIn(norm(number), norm(resp_text))

    def test_receipt_one_allocation(self):
        inv, items = self._create_invoice([('Tuition', 500, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=500, date_paid='2026-09-01', payment_method='CASH', reference_number='REF1')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('500.00'))

        resp = self.client.get(reverse('receipt-print', args=[r.pk]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        # allocation shown
        self.assertIn('Payment Allocation', content)
        self.assertIn('Tuition_v', content)
        self.assertIn('Tuition', content)
        # totals
        self.assertContainsNumber(resp, r.allocated_total())
        self.assertContainsNumber(resp, r.unallocated_amount())
        # payment method/reference shown
        self.assertIn('Cash', content)
        self.assertIn('REF1', content)

    def test_receipt_multiple_allocations(self):
        inv, items = self._create_invoice([
            ('Tuition', 3000, self.vh_tuition),
            ('Practical', 2000, self.vh_practical),
        ])
        r = Receipt.objects.create(invoice=inv, amount_paid=5000, date_paid='2026-09-02', payment_method='MPESA', reference_number='M1')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('3000.00'))
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[1], amount=Decimal('2000.00'))

        resp = self.client.get(reverse('receipt-print', args=[r.pk]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('Tuition_v', content)
        self.assertIn('Practical_v', content)
        self.assertContainsNumber(resp, r.allocated_total())
        self.assertContainsNumber(resp, r.unallocated_amount())

    def test_partially_unallocated_receipt_and_display(self):
        inv, items = self._create_invoice([('Tuition', 5000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=7000, date_paid='2026-09-03', payment_method='', reference_number='')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('5000.00'))

        resp = self.client.get(reverse('receipt-print', args=[r.pk]))
        content = resp.content.decode('utf-8')
        self.assertIn('Total Allocated', content)
        self.assertIn('Unallocated', content)
        self.assertContainsNumber(resp, r.allocated_total())
        self.assertContainsNumber(resp, r.unallocated_amount())
        # payment method empty should show 'Not specified'
        self.assertIn('Not specified', content)
        self.assertIn('Not provided', content)

    def test_completely_unallocated_receipt_display(self):
        inv, items = self._create_invoice([('Tuition', 3000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=3000, date_paid='2026-09-04')

        resp = self.client.get(reverse('receipt-print', args=[r.pk]))
        content = resp.content.decode('utf-8')
        self.assertContainsNumber(resp, r.unallocated_amount())

    def test_invoice_balance_displayed(self):
        inv, items = self._create_invoice([('Tuition', 8000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=500, date_paid='2026-09-05')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('500.00'))

        resp = self.client.get(reverse('receipt-print', args=[r.pk]))
        content = resp.content.decode('utf-8')
        # invoice balance should be shown
        bal = inv.balance()
        self.assertContainsNumber(resp, bal)

    def test_allocate_link_present(self):
        inv, items = self._create_invoice([('Tuition', 1000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=1000, date_paid='2026-09-06')
        # When unallocated, receipt page should show allocate link
        resp = self.client.get(reverse('receipt-print', args=[r.pk]))
        self.assertEqual(resp.status_code, 200)
        alloc_url = reverse('receipt-allocate', args=[r.pk])
        self.assertIn(alloc_url, resp.content.decode('utf-8'))

    def test_allocate_link_opens_allocate_page(self):
        inv, items = self._create_invoice([('Tuition', 1500, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=1500, date_paid='2026-09-07')
        alloc_resp = self.client.get(reverse('receipt-allocate', args=[r.pk]))
        self.assertEqual(alloc_resp.status_code, 200)
        # allocation page should show amount paid and allocation table header
        text = alloc_resp.content.decode('utf-8')
        self.assertIn('Allocate Receipt', text)
        self.assertContainsNumber(alloc_resp, r.amount_paid)
