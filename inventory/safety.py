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

    # The same medicine listed twice. This is the commonest duplicate of all and
    # it was the one case not checked: the salt test below skips any medicine
    # whose `generic_name` is blank, and a medicine added by typing a brand name
    # usually has none — so writing Actifed twice on one prescription produced no
    # warning at all. Keyed on the row where there is one, else the name, so it
    # also catches two catalogue entries that are the same drug typed twice.
    repeats = {}
    for m in meds:
        key = m.pk if getattr(m, 'pk', None) else (getattr(m, 'name', '') or '').strip().lower()
        if key:
            repeats[key] = repeats.get(key, 0) + 1
    for m in meds:
        key = m.pk if getattr(m, 'pk', None) else (getattr(m, 'name', '') or '').strip().lower()
        if repeats.pop(key, 0) > 1:
            warnings.append(
                f"DUPLICATE: '{m.name}' is on this list more than once.")

    seen = {}
    for m in meds:
        salt = (getattr(m, 'generic_name', '') or '').strip().lower()
        if salt:
            seen.setdefault(salt, []).append(m.name)
    for salt, names in seen.items():
        # Distinct products sharing a salt. The same product twice is already
        # reported above, and saying it again in different words is noise.
        if len(set(names)) > 1:
            warnings.append(
                f"DUPLICATE: {', '.join(sorted(set(names)))} are all '{salt}' — "
                f"same salt more than once.")

    return warnings
