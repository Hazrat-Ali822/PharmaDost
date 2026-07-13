import datetime
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone


@receiver(user_logged_in)
def set_session_expiry_to_midnight(sender, request, user, **kwargs):
    """
    On successful login, calculate the seconds remaining until midnight (12:00 AM)
    in the local timezone and set the session's expiry duration.
    This keeps the user logged in until midnight of the same day.
    """
    now = timezone.localtime(timezone.now())
    tomorrow = now.date() + datetime.timedelta(days=1)
    
    # Construct a timezone-aware midnight datetime in the active timezone
    midnight = timezone.make_aware(
        datetime.datetime.combine(tomorrow, datetime.time.min),
        timezone.get_current_timezone()
    )
    
    seconds_until_midnight = int((midnight - now).total_seconds())
    
    # Guard against negative or too-small durations
    if seconds_until_midnight < 60:
        seconds_until_midnight = 60
        
    request.session.set_expiry(seconds_until_midnight)
