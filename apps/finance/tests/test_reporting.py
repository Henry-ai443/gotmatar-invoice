from decimal import Decimal
from django.test import TestCase
from django.utils.dateparse import parse_date
from django.urls import reverse
from apps.finance.models import (
    Invoice,
    InvoiceItem,
    Receipt,
    PaymentAllocation,
    VoteHead,
    Income,
    Expense,
    ExpenseCategory,
)
from apps.finance import reports
from apps.students.models import Student
from apps.corecode.models import AcademicSession, AcademicTerm, StudentClass
from django.core.exceptions import ValidationError


class ReportingAllocationTests(TestCase):
    def setUp(self):
        # core fixtures
        self.student = Student.objects.first() or Student.objects.create(surname='T', firstname='A', registration_number='TST001')
        self.session = AcademicSession.objects.first() or AcademicSession.objects.create(name='S1')
        self.term = AcademicTerm.objects.first() or AcademicTerm.objects.create(name='Term1')
        self.sclass = StudentClass.objects.first() or StudentClass.objects.create(name='C1')

        # vote heads
        self.vh_tuition = VoteHead.objects.create(name='Tuition_test')
        self.vh_practical = VoteHead.objects.create(name='Practical_test')
        self.vh_exam = VoteHead.objects.create(name='Exam_test')
        self.vh_gmcdg = VoteHead.objects.create(name='GMCDG_test')
        self.vh_sales = VoteHead.objects.create(name='Sales_test')
        self.vh_car = VoteHead.objects.create(name='Car_test')
        self.vh_other = VoteHead.objects.create(name='Other_test')

        # expense category
        self.cat = ExpenseCategory.objects.first() or ExpenseCategory.objects.create(name='Misc')

    def _create_invoice_with_items(self, items):
        inv = Invoice.objects.create(student=self.student, session=self.session, term=self.term, class_for=self.sclass)
        created_items = []
        for desc, amt, vh in items:
            it = InvoiceItem.objects.create(invoice=inv, description=desc, amount=amt, vote_head=vh)
            created_items.append(it)
        return inv, created_items

    def test_one_receipt_one_item(self):
        inv, items = self._create_invoice_with_items([('Tuition', 15000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=15000, date_paid='2026-09-01')
        pa = PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('15000.00'))

        allocs = reports.allocations_by_vote_head()
        self.assertIn({'vote_head__name': 'Tuition_test', 'total': Decimal('15000')}, allocs)
        self.assertEqual(r.unallocated_amount(), Decimal('0.00'))

    def test_one_receipt_multiple_items(self):
        inv, items = self._create_invoice_with_items([
            ('Tuition', 1000, self.vh_tuition),
            ('Practical', 1000, self.vh_practical),
        ])
        r = Receipt.objects.create(invoice=inv, amount_paid=2000, date_paid='2026-09-01')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('1000.00'))
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[1], amount=Decimal('1000.00'))

        summary = reports.compute_summary()
        # allocations should reflect each vote head separately
        totals = {r['vote_head__name']: r['total'] for r in reports.allocations_by_vote_head()}
        self.assertEqual(totals.get('Tuition_test'), Decimal('1000'))
        self.assertEqual(totals.get('Practical_test'), Decimal('1000'))

    def test_partial_allocation_and_unallocated(self):
        inv, items = self._create_invoice_with_items([('Tuition', 3000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=2000, date_paid='2026-09-02')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('1500.00'))
        self.assertEqual(r.unallocated_amount(), Decimal('500.00'))

    def test_completely_unallocated_receipt(self):
        inv, items = self._create_invoice_with_items([('Tuition', 8000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=8000, date_paid='2026-09-03')
        # no allocations
        self.assertEqual(reports.total_allocations(), Decimal('0.00'))
        rows, unalloc_total = reports.unallocated_fee_receipts()
        # find our receipt in unallocated list
        found = any(r.id == row['receipt'].id for row in rows)
        self.assertTrue(found)

    def test_overpayment_unallocated_remainder(self):
        inv, items = self._create_invoice_with_items([('Tuition', 5000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=7000, date_paid='2026-09-04')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('5000.00'))
        self.assertEqual(r.unallocated_amount(), Decimal('2000.00'))

    def test_cross_invoice_allocation_rejected(self):
        inv1, items1 = self._create_invoice_with_items([('A', 1000, self.vh_tuition)])
        inv2, items2 = self._create_invoice_with_items([('B', 1000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv1, amount_paid=1000, date_paid='2026-09-05')
        with self.assertRaises(ValidationError):
            pa = PaymentAllocation(receipt=r, invoice_item=items2[0], amount=Decimal('1000.00'))
            pa.save()

    def test_allocation_exceeding_receipt_amount_rejected(self):
        inv, items = self._create_invoice_with_items([('A', 1000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=500, date_paid='2026-09-06')
        with self.assertRaises(ValidationError):
            pa = PaymentAllocation(receipt=r, invoice_item=items[0], amount=Decimal('1000.00'))
            pa.save()

    def test_allocation_exceeding_invoiceitem_amount_rejected(self):
        inv, items = self._create_invoice_with_items([('A', 1000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=1000, date_paid='2026-09-07')
        pa = PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('500.00'))
        # attempt to increase beyond item amount
        pa.amount = Decimal('1500.00')
        with self.assertRaises(ValidationError):
            pa.save()

    def test_editing_and_deleting_allocation(self):
        inv, items = self._create_invoice_with_items([('A', 2000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=2000, date_paid='2026-09-08')
        pa = PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('500.00'))
        pa.amount = Decimal('1000.00')
        pa.save()
        self.assertEqual(items[0].allocated_total(), Decimal('1000.00'))
        pa.delete()
        self.assertEqual(items[0].allocated_total(), Decimal('0.00'))

    def test_reduce_receipt_amount_below_allocated_rejected(self):
        inv, items = self._create_invoice_with_items([('A', 1000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=1000, date_paid='2026-09-09')
        pa = PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('1000.00'))
        r.amount_paid = Decimal('500.00')
        with self.assertRaises(ValidationError):
            r.save()

    def test_invoice_balance_never_negative(self):
        inv, items = self._create_invoice_with_items([('A', 5000, self.vh_tuition)])
        r = Receipt.objects.create(invoice=inv, amount_paid=7000, date_paid='2026-09-10')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('5000.00'))
        self.assertEqual(inv.balance(), Decimal('0.00'))

    def test_fee_totals_and_no_duplicate_on_multiple_voteheads(self):
        # One invoice with multiple items and one receipt should not double-count
        inv, items = self._create_invoice_with_items([
            ('Tuition', 15000, self.vh_tuition),
            ('Practical', 3000, self.vh_practical),
            ('Exam', 2000, self.vh_exam),
        ])
        r = Receipt.objects.create(invoice=inv, amount_paid=20000, date_paid='2026-09-11')
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[0], amount=Decimal('15000.00'))
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[1], amount=Decimal('3000.00'))
        PaymentAllocation.objects.create(receipt=r, invoice_item=items[2], amount=Decimal('2000.00'))

        summary = reports.compute_summary()
        self.assertEqual(summary['alloc_total'], Decimal('20000'))
        self.assertEqual(summary['total_fee_receipts'], summary['alloc_total'] + summary['unalloc_total'])

    def test_null_votehead_allocation_is_unallocated_cash(self):
        inv, items = self._create_invoice_with_items([
            ('Tuition', 5000, self.vh_tuition),
            ('Unassigned fee', 2000, None),
        ])
        receipt = Receipt.objects.create(invoice=inv, amount_paid=7000, date_paid='2026-09-12')
        PaymentAllocation.objects.create(
            receipt=receipt, invoice_item=items[0], amount=Decimal('5000.00')
        )
        PaymentAllocation.objects.create(
            receipt=receipt, invoice_item=items[1], amount=Decimal('2000.00')
        )

        summary = reports.compute_summary()
        allocation_rows = {
            row['vote_head__name']: row['total']
            for row in summary['fee_allocations']
        }
        unallocated_rows, unallocated_total = reports.unallocated_fee_receipts()

        self.assertEqual(allocation_rows['Tuition_test'], Decimal('5000'))
        self.assertEqual(summary['alloc_total'], Decimal('5000'))
        self.assertEqual(unallocated_total, Decimal('2000'))
        self.assertEqual(unallocated_rows[-1]['unallocated'], Decimal('2000'))
        self.assertEqual(summary['total_fee_receipts'], Decimal('7000'))

    def test_completely_unallocated_receipt_counts_as_fee_cash(self):
        inv, items = self._create_invoice_with_items([
            ('Unassigned fee', 3000, None),
        ])
        Receipt.objects.create(invoice=inv, amount_paid=3000, date_paid='2026-09-13')

        summary = reports.compute_summary()

        self.assertEqual(summary['alloc_total'], Decimal('0'))
        self.assertEqual(summary['unalloc_total'], Decimal('3000'))
        self.assertEqual(summary['total_fee_receipts'], Decimal('3000'))

    def test_completely_allocated_named_receipt_has_no_unallocated_cash(self):
        inv, items = self._create_invoice_with_items([
            ('Practical', 3000, self.vh_practical),
        ])
        receipt = Receipt.objects.create(invoice=inv, amount_paid=3000, date_paid='2026-09-14')
        PaymentAllocation.objects.create(
            receipt=receipt, invoice_item=items[0], amount=Decimal('3000.00')
        )

        summary = reports.compute_summary()

        self.assertEqual(summary['alloc_total'], Decimal('3000'))
        self.assertEqual(summary['unalloc_total'], Decimal('0'))
        self.assertEqual(summary['total_fee_receipts'], Decimal('3000'))

    def test_income_types_expenses_and_date_filtering_summary(self):
        date = '2025-01-05'
        Income.objects.create(date=date, vote_head=self.vh_gmcdg, amount=Decimal('1200.00'), description='GMCDG')
        Income.objects.create(date=date, vote_head=self.vh_sales, amount=Decimal('500.00'), description='Sales')
        Income.objects.create(date=date, vote_head=self.vh_car, amount=Decimal('300.00'), description='Car')
        Income.objects.create(date=date, vote_head=self.vh_other, amount=Decimal('150.00'), description='Other')
        Expense.objects.create(date=date, category=self.cat, amount=Decimal('400.00'), description='Office')

        df = parse_date(date); dt = parse_date(date)
        summary = reports.compute_summary(df, dt)
        # incomes aggregated
        self.assertEqual(summary['total_income'], Decimal('2150.00'))
        # expenses aggregated
        self.assertEqual(summary['total_expenses'], Decimal('400.00'))

    def _create_summary_date_fixtures(self):
        invoice, _ = self._create_invoice_with_items([])
        rows = [
            ('2025-01-01', Decimal('1000.00'), Decimal('10000.00'), Decimal('100.00')),
            ('2025-01-05', Decimal('2000.00'), Decimal('20000.00'), Decimal('200.00')),
            ('2025-01-10', Decimal('3000.00'), Decimal('30000.00'), Decimal('300.00')),
        ]
        for date, income_amount, receipt_amount, expense_amount in rows:
            Income.objects.create(
                date=date,
                vote_head=self.vh_tuition,
                amount=income_amount,
                description='Date filter fixture',
            )
            Receipt.objects.create(
                invoice=invoice,
                amount_paid=receipt_amount,
                date_paid=date,
            )
            Expense.objects.create(
                date=date,
                category=self.cat,
                amount=expense_amount,
                description='Date filter fixture',
            )

    def assert_summary_date_totals(self, date_from, date_to, expected):
        summary = reports.compute_summary(date_from, date_to)
        expense_rows = reports.expenses_by_category(date_from, date_to)
        displayed_expense_total = sum(
            row['total'] for row in expense_rows
        )

        self.assertEqual(summary['total_income'], expected[0])
        self.assertEqual(summary['total_fee_receipts'], expected[1])
        self.assertEqual(summary['total_expenses'], expected[2])
        self.assertEqual(displayed_expense_total, summary['total_expenses'])
        self.assertEqual(summary['net'], expected[0] + expected[1] - expected[2])

    def test_summary_date_from_only_filters_expenses_and_net(self):
        self._create_summary_date_fixtures()

        self.assert_summary_date_totals(
            parse_date('2025-01-05'), None,
            (Decimal('5000.00'), Decimal('50000.00'), Decimal('500.00')),
        )

    def test_summary_date_to_only_filters_expenses_and_net(self):
        self._create_summary_date_fixtures()

        self.assert_summary_date_totals(
            None, parse_date('2025-01-05'),
            (Decimal('3000.00'), Decimal('30000.00'), Decimal('300.00')),
        )

    def test_summary_both_date_bounds_filter_expenses_and_net(self):
        self._create_summary_date_fixtures()

        self.assert_summary_date_totals(
            parse_date('2025-01-05'), parse_date('2025-01-05'),
            (Decimal('2000.00'), Decimal('20000.00'), Decimal('200.00')),
        )

    def test_summary_without_date_bounds_includes_all_expenses_and_net(self):
        self._create_summary_date_fixtures()

        self.assert_summary_date_totals(
            None, None,
            (Decimal('6000.00'), Decimal('60000.00'), Decimal('600.00')),
        )

    def test_votehead_report_labels_original_receipt_as_context_only(self):
        response = self.client.get(
            reverse('votehead-report'),
            {'vote_head_id': self.vh_tuition.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Original Receipt Amount (Context Only)')
        self.assertContains(
            response,
            'This is the original receipt amount. It is shown for context only and is not the amount attributed to this VoteHead.',
        )
