from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class UserSyncAPITest(TestCase):
    def test_user_sync_creates_user_with_unusable_password_and_is_idempotent(self):
        email = "college-sync-user@example.com"
        User = get_user_model()
        User.objects.filter(email=email).delete()

        api_key = "kR8h2Xw9pQmT4vN7sL1jY6bF3cD0eA5gU2iO8xZ4wK"
        url = reverse("api-user-sync")
        payload = {"email": email, "first_name": "Jane", "last_name": "Doe"}

        response = self.client.post(url, payload, HTTP_X_API_KEY=api_key)
        self.assertEqual(response.status_code, 201)

        user = User.objects.get(email=email)
        self.assertEqual(user.username, email)
        self.assertEqual(user.first_name, "Jane")
        self.assertEqual(user.last_name, "Doe")
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_active)

        response = self.client.post(url, payload, HTTP_X_API_KEY=api_key)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email=email).count(), 1)
