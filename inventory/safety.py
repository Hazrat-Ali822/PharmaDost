"""Clinical safety screening for dispensing / prescribing.

Best-effort, non-blocking checks:
  * ALLERGY  — a medicine whose name/generic/brand contains one of the patient's
               recorded allergy terms.
  * DUPLICATE — the same salt (generic_name) appearing more than once.

Returns a list of human-readable warning strings; the caller decides whether to
show them as warnings or block. Free-text allergy fields mean this is advisory,
not a guarantee.
"""


def _allergy_terms(patient):
    if not patient or not getattr(patient, 'allergies', ''):
        return []
    raw = patient.allergies.replace(';', ',').replace('/', ',').replace('\n', ',')
    return [t.strip().lower() for t in raw.split(',') if len(t.strip()) >= 3]


def screen_medicines(patient, medicines):
    """medicines: iterable of Medicine instances (or objects with name/generic_name/brand)."""
    warnings = []
    meds = [m for m in medicines if m is not None]

    terms = _allergy_terms(patient)
    if terms:
        for m in meds:
            hay = f"{getattr(m, 'name', '')} {getattr(m, 'generic_name', '')} {getattr(m, 'brand', '')}".lower()
            for term in terms:
                if term in hay:
                    warnings.append(
                        f"ALLERGY: '{m.name}' may conflict with the patient's recorded allergy to '{term}'.")
                    break

    seen = {}
    for m in meds:
        salt = (getattr(m, 'generic_name', '') or '').strip().lower()
        if salt:
            seen.setdefault(salt, []).append(m.name)
    for salt, names in seen.items():
        if len(names) > 1:
            warnings.append(
                f"DUPLICATE: {', '.join(names)} are all '{salt}' — same salt more than once.")

    return warnings
