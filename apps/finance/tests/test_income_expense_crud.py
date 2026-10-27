from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from reportlab.pdfgen.canvas import Canvas

from apps.corecode.models import AcademicSession, AcademicTerm, StudentClass
from apps.finance.models import (
    Expense,
    ExpenseCategory,
    Income,
    Invoice,
    InvoiceItem,
    PaymentAllocation,
    Receipt,
    VoteHead,
)
from apps.students.models import Student


class IncomeExpenseCrudTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="finance-editor", password="password"
        )
        self.client.force_login(user)

        self.student = Student.objects.first() or Student.objects.create(
            surname="Finance", firstname="Tester", registration_number="FIN001"
        )
        self.session = AcademicSession.objects.first() or AcademicSession.objects.create(
            name="Finance Session"
        )
        self.term = AcademicTerm.objects.first() or AcademicTerm.objects.create(
            name="Finance Term"
        )
        self.student_class = StudentClass.objects.first() or StudentClass.objects.create(
            name="Finance Class"
        )
        self.vote_head = VoteHead.objects.create(name="General Income")
        self.other_vote_head = VoteHead.objects.create(name="Other Income")
        self.inactive_vote_head = VoteHead.objects.create(
            name="Inactive Income", is_active=False
        )
        self.category = ExpenseCategory.objects.create(name="Supplies")
        self.other_category = ExpenseCategory.objects.create(name="Repairs")
        self.inactive_category = ExpenseCategory.objects.create(
            name="Inactive Expense", is_active=False
        )

        self.income = Income.objects.create(
            date="2026-01-10",
            vote_head=self.vote_head,
            amount=Decimal("100.00"),
            description="Original income",
            source="Donor A",
        )
        self.other_income = Income.objects.create(
            date="2026-01-11",
            vote_head=self.other_vote_head,
            amount=Decimal("20.00"),
            description="Unrelated income",
        )
        self.expense = Expense.objects.create(
            date="2026-01-12",
            category=self.category,
            amount=Decimal("30.00"),
            description="Original expense",
            payee="Vendor A",
        )
        self.other_expense = Expense.objects.create(
            date="2026-01-13",
            category=self.other_category,
            amount=Decimal("5.00"),
            description="Unrelated expense",
        )

        self.invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.student_class,
        )
        self.invoice_item = InvoiceItem.objects.create(
            invoice=self.invoice,
            description="Unrelated invoice item",
            amount=Decimal("40.00"),
            vote_head=self.vote_head,
        )
        self.receipt = Receipt.objects.create(
            invoice=self.invoice,
            amount_paid=Decimal("25.00"),
            date_paid="2026-01-14",
        )
        self.allocation = PaymentAllocation.objects.create(
            receipt=self.receipt,
            invoice_item=self.invoice_item,
            amount=Decimal("25.00"),
        )

    def assert_unrelated_finance_records_unchanged(self):
        self.assertTrue(Income.objects.filter(pk=self.other_income.pk).exists())
        self.assertTrue(Expense.objects.filter(pk=self.other_expense.pk).exists())
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(InvoiceItem.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(PaymentAllocation.objects.count(), 1)
        self.receipt.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(self.receipt.amount_paid, Decimal("25.00"))
        self.assertEqual(self.allocation.amount, Decimal("25.00"))

    def test_income_update_edits_existing_record_and_reports_updated_values(self):
        response = self.client.post(
            reverse("income-update", args=[self.income.pk]),
            {
                "date": "2026-02-15",
                "vote_head": self.other_vote_head.pk,
                "amount": "250.75",
                "description": "Updated income",
                "source": "Donor B",
            },
        )

        self.assertRedirects(response, reverse("income-list"))
        self.assertEqual(Income.objects.count(), 2)
        self.income.refresh_from_db()
        self.assertEqual(self.income.date.isoformat(), "2026-02-15")
        self.assertEqual(self.income.amount, Decimal("250.75"))
        self.assertEqual(self.income.vote_head, self.other_vote_head)
        self.assertEqual(self.income.source, "Donor B")

        report_params = {"vote_head_id": self.other_vote_head.pk}
        html_report = self.client.get(reverse("votehead-report"), report_params)
        self.assertContains(html_report, "Feb. 15, 2026")
        self.assertContains(html_report, "250.75")
        drawn_text = self.request_pdf_and_capture_text(
            reverse("votehead-report-pdf"), report_params
        )
        self.assertIn("2026-02-15  250.75  Updated income", drawn_text)
        summary_params = {"date_from": "2026-02-15", "date_to": "2026-02-15"}
        summary_html = self.client.get(reverse("summary-report"), summary_params)
        self.assertContains(summary_html, "Other Income")
        self.assertContains(summary_html, "250.75")
        summary_pdf_text = self.request_pdf_and_capture_text(
            reverse("summary-report-pdf"), summary_params
        )
        self.assertIn("Other Income: 250.75", summary_pdf_text)

    def test_expense_update_edits_existing_record_and_reports_updated_values(self):
        response = self.client.post(
            reverse("expense-update", args=[self.expense.pk]),
            {
                "date": "2026-03-20",
                "category": self.other_category.pk,
                "amount": "81.25",
                "description": "Updated expense",
                "payee": "Vendor B",
            },
        )

        self.assertRedirects(response, reverse("expense-list"))
        self.assertEqual(Expense.objects.count(), 2)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.date.isoformat(), "2026-03-20")
        self.assertEqual(self.expense.amount, Decimal("81.25"))
        self.assertEqual(self.expense.category, self.other_category)
        self.assertEqual(self.expense.payee, "Vendor B")

        report_params = {"category_id": self.other_category.pk}
        html_report = self.client.get(reverse("expensecategory-report"), report_params)
        self.assertContains(html_report, "March 20, 2026")
        self.assertContains(html_report, "81.25")
        drawn_text = self.request_pdf_and_capture_text(
            reverse("expensecategory-report-pdf"), report_params
        )
        self.assertIn("2026-03-20  81.25  Updated expense  Vendor B", drawn_text)
        summary_params = {"date_from": "2026-03-20", "date_to": "2026-03-20"}
        summary_html = self.client.get(reverse("summary-report"), summary_params)
        self.assertContains(summary_html, "Repairs")
        self.assertContains(summary_html, "81.25")
        summary_pdf_text = self.request_pdf_and_capture_text(
            reverse("summary-report-pdf"), summary_params
        )
        self.assertIn("Repairs: 81.25", summary_pdf_text)

    def request_pdf_and_capture_text(self, url, params):
        draw_string = Canvas.drawString
        drawn_text = []

        def capture_text(canvas, x, y, text, *args, **kwargs):
            drawn_text.append(str(text))
            return draw_string(canvas, x, y, text, *args, **kwargs)

        with mock.patch(
            "apps.finance.views.canvas.Canvas.drawString",
            autospec=True,
            side_effect=capture_text,
        ):
            response = self.client.get(url, params)

        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        return "\n".join(drawn_text)

    def test_income_delete_requires_confirmation_and_preserves_other_records(self):
        confirmation = self.client.get(reverse("income-delete", args=[self.income.pk]))
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirm Delete")
        self.assertTrue(Income.objects.filter(pk=self.income.pk).exists())

        response = self.client.post(reverse("income-delete", args=[self.income.pk]))

        self.assertRedirects(response, reverse("income-list"))
        self.assertFalse(Income.objects.filter(pk=self.income.pk).exists())
        self.assert_unrelated_finance_records_unchanged()

    def test_expense_delete_requires_confirmation_and_preserves_other_records(self):
        confirmation = self.client.get(
            reverse("expense-delete", args=[self.expense.pk])
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirm Delete")
        self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())

        response = self.client.post(reverse("expense-delete", args=[self.expense.pk]))

        self.assertRedirects(response, reverse("expense-list"))
        self.assertFalse(Expense.objects.filter(pk=self.expense.pk).exists())
        self.assert_unrelated_finance_records_unchanged()

    def test_invalid_income_and_expense_submissions_do_not_change_records(self):
        income_response = self.client.post(
            reverse("income-update", args=[self.income.pk]),
            {
                "date": "not-a-date",
                "vote_head": self.vote_head.pk,
                "amount": "not-an-amount",
                "description": "Invalid update",
                "source": "",
            },
        )
        expense_response = self.client.post(
            reverse("expense-update", args=[self.expense.pk]),
            {
                "date": "not-a-date",
                "category": self.category.pk,
                "amount": "not-an-amount",
                "description": "Invalid update",
                "payee": "",
            },
        )

        self.assertEqual(income_response.status_code, 200)
        self.assertEqual(expense_response.status_code, 200)
        self.assertEqual(Income.objects.count(), 2)
        self.assertEqual(Expense.objects.count(), 2)
        self.income.refresh_from_db()
        self.expense.refresh_from_db()
        self.assertEqual(self.income.amount, Decimal("100.00"))
        self.assertEqual(self.income.date.isoformat(), "2026-01-10")
        self.assertEqual(self.expense.amount, Decimal("30.00"))
        self.assertEqual(self.expense.date.isoformat(), "2026-01-12")

    def test_edit_forms_reject_inactive_reassignments_but_keep_current_values(self):
        income_response = self.client.post(
            reverse("income-update", args=[self.income.pk]),
            {
                "date": "2026-01-10",
                "vote_head": self.inactive_vote_head.pk,
                "amount": "100.00",
                "description": "Original income",
                "source": "Donor A",
            },
        )
        expense_response = self.client.post(
            reverse("expense-update", args=[self.expense.pk]),
            {
                "date": "2026-01-12",
                "category": self.inactive_category.pk,
                "amount": "30.00",
                "description": "Original expense",
                "payee": "Vendor A",
            },
        )

        self.assertEqual(income_response.status_code, 200)
        self.assertEqual(expense_response.status_code, 200)
        self.assertContains(income_response, "Select a valid choice")
        self.assertContains(expense_response, "Select a valid choice")
        self.income.refresh_from_db()
        self.expense.refresh_from_db()
        self.assertEqual(self.income.vote_head, self.vote_head)
        self.assertEqual(self.expense.category, self.category)

    def test_delete_is_post_only_and_duplicate_or_invalid_ids_are_safe(self):
        income_delete_url = reverse("income-delete", args=[self.income.pk])
        expense_delete_url = reverse("expense-delete", args=[self.expense.pk])
        self.assertEqual(self.client.delete(income_delete_url).status_code, 405)
        self.assertEqual(self.client.delete(expense_delete_url).status_code, 405)
        self.assertEqual(self.client.get(reverse("income-delete", args=[999999])).status_code, 404)
        self.assertEqual(self.client.get(reverse("expense-delete", args=[999999])).status_code, 404)

        self.client.post(income_delete_url)
        self.client.post(expense_delete_url)
        self.assertEqual(self.client.post(income_delete_url).status_code, 404)
        self.assertEqual(self.client.post(expense_delete_url).status_code, 404)
        self.assert_unrelated_finance_records_unchanged()

    def test_income_and_expense_management_requires_login(self):
        self.client.logout()
        urls = (
            reverse("income-update", args=[self.income.pk]),
            reverse("income-delete", args=[self.income.pk]),
            reverse("expense-update", args=[self.expense.pk]),
            reverse("expense-delete", args=[self.expense.pk]),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)