"""Writing a prescription — shared by the live view and offline replay.

A prescription is never just its header: the medicines, the ticked lab tests and
the ticked scans all commit with it, and the appointment is closed. Offline
replay calls this same function, so an Rx written with no signal reaches the
pharmacy queue with its items intact rather than as an empty shell.
"""
from django.db import transaction


class PrescriptionResult:
    def __init__(self, prescription, medicines, n_tests, n_scans, warnings):
        self.prescription = prescription
        self.medicines = medicines
        self.n_tests = n_tests
        self.n_scans = n_scans
        self.warnings = warnings


def save_prescription(appointment, form, med_formset, user):
    """Commit a validated `PrescriptionForm` + item formset against `appointment`."""
    from inventory.safety import screen_medicines
    from prescriptions.views import _order_lab_tests, _order_scans

    patient = appointment.patient
    with transaction.atomic():
        prescription = form.save(commit=False)
        prescription.appointment = appointment
        prescription.save()

        med_formset.instance = prescription
        medicines = med_formset.save()  # blank extra rows are skipped automatically

        warnings = screen_medicines(patient, [pi.medicine for pi in medicines])

        n_tests = _order_lab_tests(patient, list(form.cleaned_data.get('tests') or []), user)
        n_scans = _order_scans(list(form.cleaned_data.get('scans') or []), patient, user)

        appointment.status = 'DONE'
        appointment.save()

    return PrescriptionResult(prescription, medicines, n_tests, n_scans, warnings)
