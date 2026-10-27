from django.conf import settings

from .models import AcademicSession, AcademicTerm, SiteConfig


def site_defaults(request):
    current_session = AcademicSession.objects.get(current=True)
    current_term = AcademicTerm.objects.get(current=True)
    vals = SiteConfig.objects.all()
    contexts = {
        "current_session": current_session.name,
        "current_term": current_term.name,
        "main_college_system_url": settings.MAIN_COLLEGE_SYSTEM_URL,
    }
    for val in vals:
        contexts[val.key] = val.value

    return contexts
