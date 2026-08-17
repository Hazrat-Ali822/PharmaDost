from django.contrib import admin

from .models import Ambulance, AmbulanceDriver, AmbulanceTrip

admin.site.register([Ambulance, AmbulanceDriver, AmbulanceTrip])
