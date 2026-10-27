from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError

from apps.corecode.models import AcademicSession, AcademicTerm, StudentClass
from apps.finance.forms import InvoiceItemForm, InvoiceItemFormset
from apps.finance.models import (
    Invoice,
    InvoiceItem,
    PaymentAllocation,
    Receipt,
    VoteHead,
)
from apps.students.models import Student


class InvoiceCreationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="invoice-tester", password="password"
        )
        self.client.force_login(user)
        self.student = Student.objects.first() or Student.objects.create(
            surname="Test", firstname="Student", registration_number="INV001"
        )
        self.session = AcademicSession.objects.first() or AcademicSession.objects.create(
            name="Session 1"
        )
        self.term = AcademicTerm.objects.first() or AcademicTerm.objects.create(
            name="Term 1"
        )
        self.sclass = StudentClass.objects.first() or StudentClass.objects.create(
            name="Class 1"
        )
        self.tuition = VoteHead.objects.create(name="Invoice Tuition")
        self.practical = VoteHead.objects.create(name="Invoice Practical")
        self.inactive = VoteHead.objects.create(
            name="Invoice Inactive", is_active=False
        )

    def invoice_data(self, total_forms=1, items=None):
        items = items or []
        data = {
            "student": self.student.pk,
            "session": self.session.pk,
            "term": self.term.pk,
            "class_for": self.sclass.pk,
            "balance_from_previous_term": "0",
            "status": "active",
            "invoiceitem_set-TOTAL_FORMS": str(total_forms),
            "invoiceitem_set-INITIAL_FORMS": "0",
            "invoiceitem_set-MIN_NUM_FORMS": "0",
            "invoiceitem_set-MAX_NUM_FORMS": "1000",
        }
        for index, item in enumerate(items):
            data.update(
                {
                    f"invoiceitem_set-{index}-description": item[0],
                    f"invoiceitem_set-{index}-amount": str(item[1]),
                    f"invoiceitem_set-{index}-vote_head": str(item[2]),
                }
            )
        return data

    def test_valid_invoice_item_with_vote_head_is_saved(self):
        response = self.client.post(
            reverse("invoice-create"),
            self.invoice_data(items=[("Tuition", 500, self.tuition.pk)]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invoice.objects.get().student, self.student)
        item = InvoiceItem.objects.get(description="Tuition")
        self.assertEqual(item.vote_head, self.tuition)

    def test_invoice_form_renders_student_autocomplete_without_full_dropdown(self):
        response = self.client.get(reverse("invoice-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="student-search-input"')
        self.assertContains(response, 'name="student"')
        self.assertNotContains(response, '<select name="student"')

    def test_shared_brand_link_uses_configured_main_college_url(self):
        with self.settings(MAIN_COLLEGE_SYSTEM_URL="https://college.example/"):
            response = self.client.get(reverse("invoice-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<a href="https://college.example/" class="brand-link">Got Matar Institute</a>',
        )

    def test_student_search_matches_partial_name_case_insensitively(self):
        matching_student = Student.objects.create(
            surname="Autocomplete",
            firstname="Mara",
            other_name="Lee",
            registration_number="ADMIT-AUTO-001",
            current_class=self.sclass,
        )

        response = self.client.get(reverse("student-search"), {"q": "mArA"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], matching_student.pk)
        self.assertEqual(response.json()["results"][0]["name"], "Autocomplete Mara Lee")
        self.assertEqual(response.json()["results"][0]["registration_number"], "ADMIT-AUTO-001")
        self.assertEqual(response.json()["results"][0]["class_name"], str(self.sclass))

    def test_student_search_matches_partial_registration_number(self):
        matching_student = Student.objects.create(
            surname="Registration",
            firstname="Search",
            registration_number="REG-XY-987654",
        )

        response = self.client.get(reverse("student-search"), {"q": "xy-987"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["id"] for row in response.json()["results"]],
            [matching_student.pk],
        )

    def test_student_search_handles_empty_no_match_and_limits_results(self):
        empty_response = self.client.get(reverse("student-search"), {"q": "   "})
        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(empty_response.json(), {"results": []})

        no_match_response = self.client.get(
            reverse("student-search"), {"q": "no-student-matches-this"}
        )
        self.assertEqual(no_match_response.json(), {"results": []})

        for index in range(12):
            Student.objects.create(
                surname=f"LimitSearch{index:02d}",
                firstname="Student",
                registration_number=f"LIMIT-{index:02d}",
            )
        limited_response = self.client.get(
            reverse("student-search"), {"q": "LimitSearch"}
        )
        self.assertEqual(len(limited_response.json()["results"]), 10)

    def test_student_search_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("student-search"), {"q": "student"})

        self.assertEqual(response.status_code, 302)

    def test_nonexistent_student_id_is_rejected_server_side(self):
        data = self.invoice_data(items=[("Tuition", 500, self.tuition.pk)])
        data["student"] = "999999"

        response = self.client.post(reverse("invoice-create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertContains(response, "Select a valid choice")

    def test_missing_vote_head_does_not_create_invoice(self):
        response = self.client.post(
            reverse("invoice-create"),
            self.invoice_data(items=[("Tuition", 500, "")]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertContains(response, "Vote Head is required.")

    def test_multiple_invoice_items_each_require_and_save_vote_head(self):
        response = self.client.post(
            reverse("invoice-create"),
            self.invoice_data(
                total_forms=2,
                items=[
                    ("Tuition", 500, self.tuition.pk),
                    ("Practical", 300, self.practical.pk),
                ],
            ),
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual(
            list(
                InvoiceItem.objects.filter(invoice=invoice).values_list(
                    "vote_head_id", flat=True
                )
            ),
            [self.tuition.pk, self.practical.pk],
        )

    def test_empty_extra_form_does_not_fail_validation(self):
        response = self.client.post(
            reverse("invoice-create"),
            self.invoice_data(
                total_forms=2,
                items=[("Tuition", 500, self.tuition.pk)],
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(InvoiceItem.objects.count(), 1)

    def test_inactive_vote_head_is_excluded_for_new_items(self):
        form = InvoiceItemForm()

        self.assertNotIn(self.inactive, form.fields["vote_head"].queryset)

    def test_existing_inactive_vote_head_remains_readable(self):
        invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        item = InvoiceItem.objects.create(
            invoice=invoice,
            description="Historical item",
            amount=100,
            vote_head=self.inactive,
        )

        form = InvoiceItemForm(instance=item)

        self.assertIn(self.inactive, form.fields["vote_head"].queryset)
        self.assertContains(self.client.get(invoice.get_absolute_url()), "Historical item")

    def test_existing_null_vote_head_remains_readable(self):
        invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            description="Historical unassigned item",
            amount=100,
            vote_head=None,
        )

        response = self.client.get(invoice.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historical unassigned item")

    def test_item_save_failure_rolls_back_parent_invoice(self):
        data = self.invoice_data(items=[("Tuition", 500, self.tuition.pk)])

        with mock.patch.object(InvoiceItem, "save", side_effect=RuntimeError("save failed")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("invoice-create"), data)

        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(InvoiceItem.objects.count(), 0)

    def test_formset_requires_vote_head_for_nonempty_row(self):
        invoice = Invoice(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        formset = InvoiceItemFormset(
            data={
                "invoiceitem_set-TOTAL_FORMS": "1",
                "invoiceitem_set-INITIAL_FORMS": "0",
                "invoiceitem_set-MIN_NUM_FORMS": "0",
                "invoiceitem_set-MAX_NUM_FORMS": "1000",
                "invoiceitem_set-0-description": "Tuition",
                "invoiceitem_set-0-amount": "500",
                "invoiceitem_set-0-vote_head": "",
            },
            instance=invoice,
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("Vote Head is required.", str(formset.errors))

    def test_delete_invoice_without_receipts(self):
        invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            description="Invoice item",
            amount=100,
            vote_head=self.tuition,
        )

        response = self.client.post(reverse("invoice-delete", args=[invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertFalse(InvoiceItem.objects.filter(invoice=invoice).exists())

    def test_delete_invoice_with_receipts(self):
        invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        receipt = Receipt.objects.create(
            invoice=invoice, amount_paid=100, date_paid="2026-09-01"
        )

        response = self.client.post(reverse("invoice-delete", args=[invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertFalse(Receipt.objects.filter(pk=receipt.pk).exists())

    def test_delete_invoice_with_allocations_preserves_other_invoice(self):
        target_invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        target_item = InvoiceItem.objects.create(
            invoice=target_invoice,
            description="Target item",
            amount=100,
            vote_head=self.tuition,
        )
        target_receipt = Receipt.objects.create(
            invoice=target_invoice, amount_paid=100, date_paid="2026-09-01"
        )
        target_allocation = PaymentAllocation.objects.create(
            receipt=target_receipt,
            invoice_item=target_item,
            amount=100,
        )

        other_invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        other_item = InvoiceItem.objects.create(
            invoice=other_invoice,
            description="Other item",
            amount=200,
            vote_head=self.practical,
        )
        other_receipt = Receipt.objects.create(
            invoice=other_invoice, amount_paid=200, date_paid="2026-09-02"
        )
        other_allocation = PaymentAllocation.objects.create(
            receipt=other_receipt,
            invoice_item=other_item,
            amount=200,
        )

        response = self.client.post(
            reverse("invoice-delete", args=[target_invoice.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Invoice.objects.filter(pk=target_invoice.pk).exists())
        self.assertFalse(InvoiceItem.objects.filter(pk=target_item.pk).exists())
        self.assertFalse(Receipt.objects.filter(pk=target_receipt.pk).exists())
        self.assertFalse(PaymentAllocation.objects.filter(pk=target_allocation.pk).exists())
        self.assertTrue(Invoice.objects.filter(pk=other_invoice.pk).exists())
        self.assertTrue(InvoiceItem.objects.filter(pk=other_item.pk).exists())
        self.assertTrue(Receipt.objects.filter(pk=other_receipt.pk).exists())
        self.assertTrue(PaymentAllocation.objects.filter(pk=other_allocation.pk).exists())

    def test_cross_invoice_allocation_refusal_rolls_back_invoice_deletion(self):
        target_invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        target_item = InvoiceItem.objects.create(
            invoice=target_invoice,
            description="Target item",
            amount=100,
            vote_head=self.tuition,
        )
        target_receipt = Receipt.objects.create(
            invoice=target_invoice, amount_paid=100, date_paid="2026-09-01"
        )

        other_invoice = Invoice.objects.create(
            student=self.student,
            session=self.session,
            term=self.term,
            class_for=self.sclass,
        )
        other_item = InvoiceItem.objects.create(
            invoice=other_invoice,
            description="Other item",
            amount=100,
            vote_head=self.practical,
        )
        other_receipt = Receipt.objects.create(
            invoice=other_invoice, amount_paid=100, date_paid="2026-09-02"
        )
        cross_allocation = PaymentAllocation.objects.create(
            receipt=target_receipt,
            invoice_item=target_item,
            amount=Decimal("50.00"),
        )
        cross_allocation.receipt = target_receipt
        cross_allocation.invoice_item = other_item
        PaymentAllocation.objects.bulk_update(
            [cross_allocation], ["receipt", "invoice_item"]
        )

        with self.assertRaises(ProtectedError):
            self.client.post(reverse("invoice-delete", args=[target_invoice.pk]))

        self.assertTrue(Invoice.objects.filter(pk=target_invoice.pk).exists())
        self.assertTrue(InvoiceItem.objects.filter(pk=target_item.pk).exists())
        self.assertTrue(Receipt.objects.filter(pk=target_receipt.pk).exists())
        self.assertTrue(Invoice.objects.filter(pk=other_invoice.pk).exists())
        self.assertTrue(InvoiceItem.objects.filter(pk=other_item.pk).exists())
        self.assertTrue(Receipt.objects.filter(pk=other_receipt.pk).exists())
        self.assertTrue(PaymentAllocation.objects.filter(pk=cross_allocation.pk).exists())