from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from pharma_mgmt.pagination import paginate

from accounts.decorators import role_required, feature_required
from django.utils import timezone
from .forms import PatientForm, ClinicalRecordForm
from .search import apply_search
from .models import Patient


def _visible_patients(user):
    """Doctors only see patients assigned to them (via appointments);
    Lab Techs only see patients with lab orders;
    Sonographers only see patients with scan studies;
    everyone else (admin/reception/etc.) sees all."""
    qs = Patient.objects.filter(is_active=True)
    role = getattr(user, 'role', None)
    if user.is_superuser:
        return qs
        
    if role == 'DOCTOR':
        qs = qs.filter(appointments__doctor__user=user).distinct()
    elif role == 'LABTECH':
        qs = qs.filter(lab_orders__isnull=False).distinct()
    elif role == 'SONOGRAPHER':
        qs = qs.filter(imaging_studies__isnull=False).distinct()
    return qs


@feature_required('patients')
def patient_list(request):
    q = request.GET.get('q', '').strip()
    patients = _visible_patients(request.user)
    # One shared implementation — see patients/search.py for why a plain
    # `phone__icontains` and a single-substring name match both fail on real data.
    patients = apply_search(patients, q)
    is_doctor = getattr(request.user, 'role', None) == 'DOCTOR' and not request.user.is_superuser
    page = paginate(request, patients)
    return render(request, 'patients/patient_list.html',
                  {'patients': page, 'page_obj': page, 'q': q, 'is_doctor': is_doctor})


@feature_required('patients')
def patient_index(request):
    """Compact JSON of the tenant's patient registry, for OFFLINE search.

    The list is paginated, so the service worker only caches one page and an
    offline search would otherwise see just those ~25 rows. The logged-in browser
    fetches this while online and keeps it on the device (localStorage), so an
    offline search runs against the WHOLE registry. Scoped exactly like
    `patient_list` through `_visible_patients` (tenant + role narrowing), so it
    exposes nothing the list itself wouldn't. Capped so a very large registry
    cannot bloat the device or this response."""
    from django.http import JsonResponse
    rows = [{
        'pk': p.pk,
        'mrn': p.mrn or '',
        'name': p.full_name or '',
        'phone': p.phone or '',
        'cnic': p.cnic or '',
        'gender': p.get_gender_display() if p.gender else '',
        'age': p.age_display or '',
    } for p in _visible_patients(request.user).order_by('-id')[:5000]]
    resp = JsonResponse({'patients': rows}, json_dumps_params={'ensure_ascii': False})
    resp['Cache-Control'] = 'private, max-age=0'
    return resp


@feature_required('patients')
@role_required(['ADMIN', 'RECEPTIONIST'])
def patient_create(request):
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Patient registered successfully.')
            return redirect('patient_list')
    else:
        form = PatientForm()
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Register Patient'})


@feature_required('patients')
def patient_edit(request, pk):
    # scope to the tenant + a doctor's own patients, same as patient_detail
    patient = _get_scoped_patient(request, pk)
    if request.method == 'POST':
        form = PatientForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            messages.success(request, 'Patient updated successfully.')
            return redirect('patient_list')
    else:
        form = PatientForm(instance=patient)
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Edit Patient', 'patient': patient})


@feature_required('patients')
@role_required(['ADMIN'])
def patient_delete(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    
    # Check if patient has any history
    has_history = False
    if patient.appointments.exists():
        has_history = True
    elif hasattr(patient, 'invoices') and patient.invoices.exists():
        has_history = True
    elif hasattr(patient, 'lab_orders') and patient.lab_orders.exists():
        has_history = True
    elif hasattr(patient, 'imaging_studies') and patient.imaging_studies.exists():
        has_history = True
    elif hasattr(patient, 'pharmacy_sales') and patient.pharmacy_sales.exists():
        has_history = True
        
    if request.method == 'POST':
        action = request.POST.get('action', 'archive')
        if action == 'delete' and not has_history:
            name = patient.full_name
            patient.delete()
            messages.success(request, f"Patient '{name}' was permanently deleted.")
            return redirect('patient_list')
        else:
            # Soft delete
            patient.is_active = False
            patient.save()
            messages.success(request, f"Patient '{patient.full_name}' has been archived.")
            return redirect('patient_list')
            
    return render(request, 'patients/patient_confirm_delete.html', {
        'patient': patient,
        'has_history': has_history
    })


def _get_scoped_patient(request, pk):
    """404 if not found, 403 if a doctor tries to open a patient not assigned to them."""
    patient = get_object_or_404(Patient, pk=pk)
    if not _visible_patients(request.user).filter(pk=pk).exists():
        raise PermissionDenied("You can only view your own patients.")
    return patient


@feature_required('patients')
def patient_detail(request, pk):
    from prescriptions.models import Prescription
    from billing.models import Invoice
    patient = _get_scoped_patient(request, pk)

    appointments = patient.appointments.select_related('doctor').order_by('-appointment_date', '-created_at')
    prescriptions = (Prescription.objects
                     .filter(appointment__patient=patient)
                     .select_related('appointment', 'appointment__doctor')
                     .prefetch_related('items', 'items__medicine')
                     .order_by('-created_at'))
    lab_orders = (patient.lab_orders
                  .prefetch_related('results', 'results__lab_test')
                  .order_by('-order_date'))
    clinical = patient.clinical_records.select_related('doctor', 'created_by').all()
    imaging_studies = (patient.imaging_studies
                       .select_related('referred_by', 'performed_by')
                       .order_by('-study_date'))
    invoices = Invoice.all_objects.filter(patient=patient).prefetch_related('items').order_by('-created_at')
    pharmacy_sales = (patient.pharmacy_sales
                      .prefetch_related('items', 'items__medicine')
                      .order_by('-created_at'))
    documents = patient.documents.select_related('uploaded_by').all()

    # Gather chronological vital signs history (OPD + IPD)
    import json
    vitals_history = []
    
    # 1. OPD Clinical Records
    for record in clinical:
        if record.temperature or record.bp or record.pulse:
            dt_str = record.date.strftime("%Y-%m-%d") if record.date else "—"
            # temperature/pulse are free-text CharFields ("98.6°F", "80 bpm"),
            # so parse defensively — a non-numeric entry must not 500 the EMR page.
            temp_val = None
            try:
                temp_val = float(str(record.temperature).replace('°F', '').replace('F', '').strip())
            except (ValueError, AttributeError):
                pass
            pulse_val = None
            try:
                pulse_val = int(float(str(record.pulse).replace('bpm', '').strip()))
            except (ValueError, AttributeError):
                pass
            vitals_history.append({
                'source': 'OPD',
                'date': dt_str,
                'temp': temp_val,
                'bp': record.bp or '',
                'pulse': pulse_val,
            })
            
    # 2. IPD Daily Rounds
    from ipd.models import Admission, DoctorRound
    admissions = Admission.objects.filter(patient=patient)
    rounds = DoctorRound.objects.filter(admission__in=admissions).order_by('round_time')
    for round_rec in rounds:
        if round_rec.vitals_temp or round_rec.vitals_bp or round_rec.vitals_pulse:
            temp_val = None
            try:
                temp_val = float(round_rec.vitals_temp.replace('°F', '').strip())
            except (ValueError, AttributeError):
                pass
            pulse_val = None
            try:
                pulse_val = int(round_rec.vitals_pulse.replace('bpm', '').strip())
            except (ValueError, AttributeError):
                pass
                
            dt_str = round_rec.round_time.strftime("%Y-%m-%d %H:%M") if round_rec.round_time else "—"
            vitals_history.append({
                'source': 'IPD',
                'date': dt_str,
                'temp': temp_val,
                'bp': round_rec.vitals_bp or '',
                'pulse': pulse_val,
            })

    # Sort chronological
    vitals_history.sort(key=lambda x: x['date'])
    vitals_json = json.dumps(vitals_history)

    return render(request, 'patients/patient_detail.html', {
        'patient': patient,
        'appointments': appointments,
        'prescriptions': prescriptions,
        'lab_orders': lab_orders,
        'clinical': clinical,
        'imaging_studies': imaging_studies,
        'invoices': invoices,
        'pharmacy_sales': pharmacy_sales,
        'documents': documents,
        'vitals_json': vitals_json,
    })


@role_required(["ADMIN", "DOCTOR"])
def record_add(request, pk):
    patient = _get_scoped_patient(request, pk)
    if request.method == 'POST':
        form = ClinicalRecordForm(request.POST)
        if form.is_valid():
            rec = form.save(commit=False)
            rec.patient = patient
            rec.created_by = request.user
            # link the doctor profile if the logged-in user is a doctor
            doctor = getattr(request.user, 'doctor', None)
            if doctor is not None:
                rec.doctor = doctor
            rec.save()
            messages.success(request, 'Clinical record added to history.')
            return redirect('patient_detail', pk=patient.pk)
    else:
        form = ClinicalRecordForm()
    return render(request, 'patients/record_form.html', {'form': form, 'patient': patient})


# --------------------------------------------------------------- documents
# Photographs of paper: the prescription a doctor wrote by hand, a lab report
# brought from outside, an ID card. Nothing is read out of the image — see the
# note on `PatientDocument`. Gated on `patients` **or** `pos`, because the
# pharmacist is often the one holding the paper and does not hold `patients`.

@feature_required('patients', 'pos')
def document_add(request, pk):
    from .forms import PatientDocumentForm
    from .images import compress, make_thumbnail

    patient = _document_patient(request, pk)
    if request.method == 'POST':
        form = PatientDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.patient = patient
            doc.uploaded_by = request.user
            doc.hospital = patient.hospital
            if doc.image:
                doc.image = compress(doc.image)
                # Built from the compressed copy, not the original upload, so
                # the rotation and colour conversion are already applied.
                doc.thumbnail = make_thumbnail(doc.image)
            appt_id = request.POST.get('appointment_id')
            if appt_id:
                doc.appointment = patient.appointments.filter(pk=appt_id).first()
            doc.save()
            messages.success(request, 'Photo saved to the patient record.')
            return redirect(_safe_next(request, patient))
    else:
        form = PatientDocumentForm(initial={'kind': request.GET.get('kind') or 'RX'})
    return render(request, 'patients/document_form.html', {
        'form': form, 'patient': patient,
        'appointment_id': request.GET.get('appointment_id') or '',
        'next': request.GET.get('next') or '',
    })


@feature_required('patients', 'pos')
def document_file(request, pk):
    """Stream a patient document, behind the login and the tenant scope.

    These are **not** served from `MEDIA_URL`, and that is deliberate twice over.

    Access: a prescription photograph is a medical record. Anything under
    `/media/` is fetched by the web server with no session, so it would be
    readable by anyone holding the URL — including someone who left the
    hospital's employment. The path contains a uuid4 and so is not guessable,
    but "hard to guess" is not access control for a patient's records.

    Availability: `django.conf.urls.static.static()` — the line at the bottom of
    `pharma_mgmt/urls.py` — **returns an empty list when `DEBUG` is False**. On
    the host DEBUG is off, so nothing in Django serves `/media/` there at all,
    and whether the file appears depends on web-server configuration nobody
    checked. Going through a view makes it work identically on the hosted site,
    the desktop build and the LAN, with no Apache config to get right.
    """
    from django.http import FileResponse, Http404
    from .models import PatientDocument

    doc = get_object_or_404(PatientDocument.objects.select_related('patient'), pk=pk)
    _document_patient(request, doc.patient_id)      # tenant + role scope, or 403/404

    # `?thumb=1` asks for the small copy. Falling back to the full picture keeps
    # rows that predate thumbnails, and rows whose thumbnail could not be made,
    # working rather than blank.
    field = doc.image
    if request.GET.get('thumb') and doc.thumbnail:
        field = doc.thumbnail
    try:
        handle = field.open('rb')
    except (FileNotFoundError, ValueError):
        # A database restored without its media, or a wiped upload folder.
        raise Http404('That picture is no longer on disk.')
    response = FileResponse(handle, content_type='image/jpeg')
    # Private: it is a patient record, so no shared proxy may keep a copy. Still
    # cached by the browser, or a record with a dozen photos re-fetches them all
    # on every visit.
    response['Cache-Control'] = 'private, max-age=600'
    return response


@feature_required('patients', 'pos')
def document_delete(request, pk):
    """Remove a photo. The file goes with the row — a wrong or duplicate picture
    left on disk is a record of nothing, and this is not a clinical entry whose
    history has to survive."""
    from .models import PatientDocument

    doc = get_object_or_404(PatientDocument.objects.select_related('patient'), pk=pk)
    _document_patient(request, doc.patient_id)      # tenant + role scope
    if request.method == 'POST':
        if not (request.user.is_superuser or request.user.role == 'ADMIN'
                or doc.uploaded_by_id == request.user.pk):
            raise PermissionDenied('Only an admin or whoever uploaded it can remove a photo.')
        patient_id = doc.patient_id
        doc.image.delete(save=False)
        if doc.thumbnail:
            doc.thumbnail.delete(save=False)
        doc.delete()
        messages.success(request, 'Photo removed.')
        return redirect('patient_detail', pk=patient_id)
    return redirect('patient_detail', pk=doc.patient_id)


def _safe_next(request, patient):
    """Where to go after saving — back where the user was, if that is our page.

    `next` arrives in the request, so it is checked before being followed:
    without `url_has_allowed_host_and_scheme` a link like
    `?next=https://evil.example/` turns this form into an open redirect, which
    is a phishing step (the user is on the real hospital domain, saves a photo,
    and lands on a copy of the login page).
    """
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return target
    return reverse('patient_detail', args=[patient.pk])


def _document_patient(request, pk):
    """The patient, scoped — reusing the same rule as every other patient screen.

    A pharmacist holds `pos` but not `patients`, so `_visible_patients` would
    show them everyone; that is correct here (they serve whoever walks up to the
    counter) and the tenant filter still applies through `Patient.objects`.
    """
    return _get_scoped_patient(request, pk)


@feature_required('patients', 'prescriptions', 'opd', 'ipd')
def patient_timeline_json(request, pk):
    """JSON feed for the Clinical Timeline Drawer on Doctor Rx and EMR screens."""
    from django.http import JsonResponse
    from prescriptions.models import Prescription

    patient = _get_scoped_patient(request, pk)
    events = []

    # 1. Prescriptions
    rxs = (Prescription.objects
           .filter(appointment__patient=patient)
           .select_related('appointment', 'appointment__doctor')
           .prefetch_related('items', 'items__medicine')
           .order_by('-created_at')[:15])
    for rx in rxs:
        med_list = []
        for it in rx.items.all():
            m_name = it.medicine.name if it.medicine else it.custom_medicine_name
            dose = f" ({it.dosage})" if it.dosage else ""
            med_list.append(f"{m_name}{dose}")
        events.append({
            'type': 'rx',
            'icon': '💊',
            'date': rx.created_at.strftime('%d/%m/%Y %H:%M'),
            'timestamp': rx.created_at.timestamp(),
            'title': f"Prescription by Dr. {rx.appointment.doctor.full_name if rx.appointment and rx.appointment.doctor else '—'}",
            'subtitle': f"Diagnosis: {rx.diagnosis or '—'}",
            'desc': ', '.join(med_list) if med_list else 'No medicines entered.',
            'badge': rx.status,
        })

    # 2. Lab Orders
    lab_orders = patient.lab_orders.prefetch_related('results', 'results__lab_test').order_by('-order_date')[:15]
    for lo in lab_orders:
        tests = [r.lab_test.name for r in lo.results.all() if r.lab_test]
        events.append({
            'type': 'lab',
            'icon': '🧪',
            'date': lo.order_date.strftime('%d/%m/%Y') if lo.order_date else '—',
            'timestamp': lo.order_date.toordinal() if lo.order_date else 0,
            'title': f"Lab Order #{lo.id}",
            'subtitle': f"Status: {lo.status}",
            'desc': ', '.join(tests) if tests else 'Tests pending',
            'badge': lo.status,
        })

    # 3. OPD Visits
    appts = patient.appointments.select_related('doctor').order_by('-appointment_date')[:15]
    for a in appts:
        events.append({
            'type': 'opd',
            'icon': '🩺',
            'date': a.appointment_date.strftime('%d/%m/%Y') if a.appointment_date else '—',
            'timestamp': a.appointment_date.toordinal() if a.appointment_date else 0,
            'title': f"OPD Consultation — Dr. {a.doctor.full_name if a.doctor else '—'}",
            'subtitle': f"Token #{a.token_number or '—'}",
            'desc': f"Department: {a.doctor.department.name if a.doctor and a.doctor.department else 'General'}",
            'badge': a.status,
        })

    # Sort events newest first
    events.sort(key=lambda x: x.get('timestamp', 0), reverse=True)

    return JsonResponse({
        'patient': {
            'id': patient.id,
            'name': patient.full_name,
            'mrn': patient.mrn or '—',
            'gender': patient.get_gender_display(),
            'age': patient.age_display,
            'allergies': patient.allergies or 'None',
        },
        'events': events[:30]
    })


def patient_portal_hub(request, token):
    """Public read-only Digital Patient Health Portal.
    No login required; authenticated by the unguessable cryptographically secure UUID token.
    Displays patient's prescriptions, verified diagnostic lab results, ultrasound/x-ray scans, and invoices.
    """
    try:
        patient = Patient.objects.select_related('hospital', 'panel').filter(portal_token=token, is_active=True).first()
    except Exception:
        patient = None

    if not patient:
        from django.http import Http404
        raise Http404("Patient health record not found.")

    hospital = getattr(patient, 'hospital', None)

    # 0. Active Today's OPD Token
    active_appointment = None
    patients_ahead = 0
    current_token = None
    try:
        from opd.models import Appointment
        today = timezone.localdate()
        today_appts = Appointment.objects.filter(
            patient=patient,
            appointment_date=today
        ).select_related('doctor', 'doctor__department').order_by('-id')
        active_appointment = today_appts.first()

        if active_appointment and active_appointment.status not in ['DONE', 'CANCELLED', 'NO_SHOW']:
            doc_appts = Appointment.objects.filter(
                doctor=active_appointment.doctor,
                appointment_date=today
            ).order_by('token_no')
            in_consult = doc_appts.filter(status='IN_CONSULT').first()
            if in_consult:
                current_token = in_consult.token_no

            patients_ahead = doc_appts.filter(
                status__in=['BOOKED', 'ARRIVED'],
                token_no__lt=active_appointment.token_no
            ).count()
    except Exception:
        active_appointment = None

    # 1. Prescriptions with medicines
    try:
        from prescriptions.models import Prescription
        prescriptions = (Prescription.objects
                         .filter(appointment__patient=patient)
                         .select_related('appointment__doctor')
                         .prefetch_related('items__medicine')
                         .order_by('-created_at'))
    except Exception:
        prescriptions = []

    # 2. Lab Orders with verified test results
    try:
        from lab.models import TestOrder
        lab_orders = (TestOrder.objects
                      .filter(patient=patient)
                      .select_related('ordered_by')
                      .prefetch_related('results__lab_test__category')
                      .order_by('-order_date'))
    except Exception:
        lab_orders = []

    # 3. Radiology Scans
    try:
        from imaging.models import ImagingStudy
        imaging_studies = (ImagingStudy.objects
                           .filter(patient=patient)
                           .select_related('referred_by', 'performed_by')
                           .order_by('-study_date'))
    except Exception:
        imaging_studies = []

    # 4. Invoices
    try:
        from billing.models import Invoice
        invoices = (Invoice.objects
                    .filter(patient=patient)
                    .order_by('-created_at'))
    except Exception:
        invoices = []

    return render(request, 'patients/portal_hub.html', {
        'patient': patient,
        'hospital': hospital,
        'active_appointment': active_appointment,
        'patients_ahead': patients_ahead,
        'current_token': current_token,
        'prescriptions': prescriptions,
        'lab_orders': lab_orders,
        'imaging_studies': imaging_studies,
        'invoices': invoices,
    })


from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def patient_portal_lookup(request):
    """Public Portal Lookup Page for patients who lost their printed slip/receipt.
    Allows searching by Mobile Phone Number, MRN, CNIC, or Patient Name.
    Works seamlessly across all hospital tenants.
    """
    error = None
    results = None
    query = (request.GET.get('query') or request.POST.get('query') or '').strip()

    # Which hospital's register is being searched. The host answers this when
    # the tenant has a subdomain, but wildcard DNS is not guaranteed to be set
    # up (see CLAUDE.md — the `/<slug>/login/` path form exists for exactly that
    # reason), so the patient can also pick. Never "search them all": that made
    # this a search box over every customer's patient register.
    from saas.models import Hospital
    from saas.utils import get_current_hospital, hospital_from_host

    hospital = get_current_hospital() or hospital_from_host(request.get_host())
    if not hospital:
        chosen = (request.GET.get('hospital') or request.POST.get('hospital') or '').strip()
        if chosen:
            hospital = Hospital.objects.filter(slug=chosen, is_active=True).first()
    if not hospital and request.user.is_authenticated:
        hospital = getattr(request.user, 'hospital', None)

    # A single-site install (the desktop / LAN build) has no Hospital rows at
    # all and files its patients under `hospital IS NULL`; there is nothing to
    # choose and nothing to leak.
    hospitals = list(Hospital.objects.filter(is_active=True).order_by('name'))         if not hospital else []

    if query and not hospital and hospitals:
        return render(request, 'patients/portal_lookup.html', {
            'error': 'Please choose your hospital first.',
            'query': query, 'results': None, 'hospitals': hospitals,
        })

    if query:
        # Rate limit to prevent automated scraping of sequential MRNs
        from django.core.cache import cache
        ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
              or request.META.get('REMOTE_ADDR', '127.0.0.1'))
        cache_key = f"portal_lookup_rate:{ip}"
        try:
            attempts = cache.get(cache_key, 0)
            if attempts >= 20:
                return render(request, 'patients/portal_lookup.html', {
                    'error': 'Too many search attempts. Please wait 1 minute before trying again.',
                    'query': query, 'results': None,
                    'hospitals': hospitals, 'hospital': hospital,
                })
            cache.set(cache_key, attempts + 1, timeout=60)
        except Exception:
            pass
        digits = ''.join(c for c in query if c.isdigit())

        # A name is not a secret, and what this page hands back is a medical
        # record. Matching on a name alone meant anyone could type "Muhammad",
        # get a list of real patients, and open their prescriptions, lab results
        # and bills. The query has to carry something the patient actually holds
        # and that is printed on their slip: the mobile number, the full MRN, or
        # the CNIC. A name still narrows a common number; it just cannot be the
        # whole of it.
        #
        # "at least 7 digits, or an MRN in its printed form" rather than "any
        # digits": a bare "9" is not a credential, it is the first step of
        # counting upwards, and a single match opens the record.
        looks_like_mrn = '-' in query and len(digits) >= 3
        if len(digits) < 7 and not looks_like_mrn:
            return render(request, 'patients/portal_lookup.html', {
                'error': ("Please enter your mobile number, or the MRN exactly "
                          "as printed on your slip (for example SD-000009). "
                          "A name on its own is not enough to open a health "
                          "record."),
                'query': query, 'results': None,
                'hospitals': hospitals, 'hospital': hospital,
            })

        # Fail-closed, exactly as `TenantManager` does. `Patient.objects` IS a
        # TenantManager, but this page is ANONYMOUS: nothing binds a tenant and
        # the thread is not strict, so the manager hands back every hospital's
        # patients. On the bare platform domain that made this a search box over
        # every customer's patient register — name, hospital and MRN in the
        # result list, and a single match redirected straight into the full
        # record. A hospital-less install (desktop/LAN) matches its own
        # `hospital IS NULL` rows and keeps working.
        patients_qs = Patient.objects.filter(is_active=True).select_related('hospital')
        if hospital:
            patients_qs = patients_qs.filter(hospital=hospital)
        else:
            patients_qs = patients_qs.filter(hospital__isnull=True)

        q_filter = Q(mrn__iexact=query) | Q(cnic__icontains=query)

        # The MRN as printed, and the bare number inside it. Deliberately NOT
        # `Q(id=num)`: the patient never sees the internal row id, so it can
        # only ever be guessed at, and guessing is what this is protecting
        # against.
        if looks_like_mrn or query.isdigit():
            tail = digits.lstrip('0') or '0'
            if tail.isdigit() and len(tail) <= 9:
                q_filter |= (Q(mrn__endswith=f"-{int(tail):06d}")
                             | Q(mrn__endswith=f"-{tail}"))

        # Phone digits matching
        if len(digits) >= 7:
            q_filter |= Q(phone__icontains=digits) | Q(phone__icontains=digits[-7:])
            if len(digits) >= 10:
                q_filter |= Q(phone__icontains=digits[-10:])

        # Multi-word name matching
        words = [w for w in query.split() if w]
        if words and not query.isdigit():
            name_q = Q()
            for w in words:
                name_q &= Q(full_name__icontains=w)
            q_filter |= name_q

        matched_qs = patients_qs.filter(q_filter).distinct()
        count = matched_qs.count()

        if count == 1:
            patient = matched_qs.first()
            if patient and patient.portal_token:
                return redirect('patient_portal_hub', token=patient.portal_token)
        elif count > 1:
            results = matched_qs[:10]
        else:
            error = (f"No patient records found matching '{query}' at this "
                     f"hospital. Please check your Mobile Number or MRN — and "
                     f"make sure you are using your own hospital's portal link "
                     f"(the QR code on your slip opens the right one).")

    return render(request, 'patients/portal_lookup.html', {
        'error': error,
        'query': query,
        'results': results,
        'hospitals': hospitals,
        'hospital': hospital,
    })



