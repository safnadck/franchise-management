from django.core.mail import send_mail
from django.conf import settings

def send_welcome_email(user):
    subject = "Welcome to EzfinTutor!"
    message = f"""
    Hi {user.get_full_name() or user.username},

    Welcome to EzfinTutor! Your account has been created successfully.

    You can now log in and start your learning journey.

    Best regards,
    EzfinTutor Team
    """
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)

def send_enrollment_email(user, course_name):
    subject = "Enrollment Confirmation"
    message = f"""
    Hi {user.get_full_name() or user.username},

    You have been successfully enrolled in the course:
    {course_name}

    Visit your dashboard to start learning.

    Best wishes,
    EzfinTutor Team
    """
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
