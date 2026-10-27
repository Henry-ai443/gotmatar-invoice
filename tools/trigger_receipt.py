from django.test import Client
import requests
from django.contrib.auth import get_user_model
from apps.finance.models import Invoice
from django.utils import timezone

User = get_user_model()
u = User.objects.first()
print('user', u)
inv = Invoice.objects.filter(pk=11).first()
print('invoice', inv)
assert inv is not None, 'invoice 11 not found'

c = Client()
c.force_login(u)

orig = requests.post

def wrapped(url, *a, **kw):
    r = orig(url, *a, **kw)
    print('OUTBOUND_REQUEST_STATUS', r.status_code)
    try:
        print('OUTBOUND_RESPONSE_TEXT', r.text)
    except Exception:
        pass
    return r

requests.post = wrapped

resp = c.post('/finance/receipt/create?invoice=11', {'amount_paid': '100', 'date_paid': str(timezone.now().date()), 'comment': 'test'}, follow=True)
print('CLIENT_STATUS', resp.status_code)
print('CLIENT_REDIRECTS', resp.redirect_chain)
print('CLIENT_CONTENT_SNIPPET', resp.content[:500])
