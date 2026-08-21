from decimal import Decimal
from django.db import transaction

# Standard Laboratory Categories, Tests, Units, Reference Ranges and Default PKR Prices
LAB_CATALOG = {
    "Hematology": [
        ("Complete Blood Count (CBC)", "Profile", "Hb: 12.0–16.5 g/dL | TLC: 4000–11000 /uL | PLT: 150–450k", Decimal("800.00")),
        ("Hemoglobin (Hb)", "g/dL", "Male: 13.5–17.5 | Female: 12.0–15.5", Decimal("300.00")),
        ("ESR (Erythrocyte Sedimentation Rate)", "mm/1st hr", "Male: 0–15 | Female: 0–20", Decimal("300.00")),
        ("Blood Group & Rh Factor", "—", "A / B / AB / O (Positive / Negative)", Decimal("300.00")),
        ("Platelet Count", "10^9/L", "150–450", Decimal("300.00")),
        ("Peripheral Blood Smear (PBS)", "—", "Normocytic, Normochromic", Decimal("600.00")),
        ("Reticulocyte Count", "%", "0.5–2.5 %", Decimal("400.00")),
    ],
    "Coagulation Profile": [
        ("Prothrombin Time (PT / INR)", "Sec / Ratio", "Control: 11–13.5 sec | INR: 0.8–1.2", Decimal("800.00")),
        ("APTT (Partial Thromboplastin Time)", "Seconds", "25–35 sec", Decimal("800.00")),
        ("Bleeding Time & Clotting Time (BT/CT)", "Minutes", "BT: 2–7 min | CT: 4–10 min", Decimal("400.00")),
        ("D-Dimer", "ug/mL", "< 0.5 ug/mL (Negative)", Decimal("2200.00")),
    ],
    "Biochemistry & Diabetes": [
        ("Fasting Blood Glucose (BSF)", "mg/dL", "70–100 mg/dL", Decimal("200.00")),
        ("Random Blood Glucose (BSR)", "mg/dL", "< 140 mg/dL (Normal)", Decimal("150.00")),
        ("HbA1c (Glycated Hemoglobin)", "%", "Normal: <5.7% | Prediabetic: 5.7–6.4% | Diabetic: >6.5%", Decimal("1500.00")),
        ("Oral Glucose Tolerance Test (OGTT)", "mg/dL", "Fasting: <100 | 2 hr: <140 mg/dL", Decimal("600.00")),
        ("Serum Uric Acid", "mg/dL", "Male: 3.4–7.0 | Female: 2.4–6.0", Decimal("400.00")),
        ("Serum Calcium (Total)", "mg/dL", "8.5–10.5 mg/dL", Decimal("500.00")),
        ("Serum Electrolytes (Na+, K+, Cl-)", "mmol/L", "Na: 135–145 | K: 3.5–5.0 | Cl: 98–106", Decimal("1200.00")),
        ("Serum Magnesium", "mg/dL", "1.7–2.2 mg/dL", Decimal("600.00")),
        ("Serum Phosphorus", "mg/dL", "2.5–4.5 mg/dL", Decimal("500.00")),
    ],
    "Liver Function Tests (LFT)": [
        ("LFT Complete Profile", "Profile", "Bilirubin: 0.2–1.2 | SGPT: <42 | Alk Phos: 40–130", Decimal("1200.00")),
        ("Total Bilirubin", "mg/dL", "0.2–1.2 mg/dL", Decimal("350.00")),
        ("Direct Bilirubin", "mg/dL", "0.0–0.3 mg/dL", Decimal("350.00")),
        ("ALT / SGPT", "U/L", "Male: < 45 | Female: < 34", Decimal("400.00")),
        ("AST / SGOT", "U/L", "0–40 U/L", Decimal("400.00")),
        ("Alkaline Phosphatase (ALP)", "U/L", "40–130 U/L", Decimal("450.00")),
        ("Serum Albumin", "g/dL", "3.5–5.0 g/dL", Decimal("400.00")),
        ("Total Protein", "g/dL", "6.0–8.3 g/dL", Decimal("400.00")),
    ],
    "Renal Function Tests (RFT / KFT)": [
        ("RFT Complete Profile", "Profile", "Urea: 15–45 mg/dL | Creatinine: 0.6–1.2 mg/dL", Decimal("1000.00")),
        ("Serum Creatinine", "mg/dL", "Male: 0.7–1.3 | Female: 0.6–1.1", Decimal("350.00")),
        ("Blood Urea", "mg/dL", "15–45 mg/dL", Decimal("350.00")),
        ("Blood Urea Nitrogen (BUN)", "mg/dL", "7–20 mg/dL", Decimal("350.00")),
    ],
    "Lipid Profile": [
        ("Lipid Profile Complete", "Profile", "Chol: <200 | TG: <150 | HDL: >40 | LDL: <100", Decimal("1500.00")),
        ("Total Cholesterol", "mg/dL", "< 200 mg/dL (Desirable)", Decimal("400.00")),
        ("Serum Triglycerides", "mg/dL", "< 150 mg/dL (Normal)", Decimal("450.00")),
        ("HDL Cholesterol", "mg/dL", "Male: > 40 | Female: > 50", Decimal("400.00")),
        ("LDL Cholesterol", "mg/dL", "< 100 mg/dL (Optimal)", Decimal("400.00")),
    ],
    "Serology & Infectious Diseases": [
        ("HBsAg (Hepatitis B Surface Antigen)", "ICT", "Non-Reactive (Negative)", Decimal("500.00")),
        ("Anti-HCV (Hepatitis C Antibody)", "ICT", "Non-Reactive (Negative)", Decimal("500.00")),
        ("HIV 1 & 2 Antibodies", "ICT", "Non-Reactive (Negative)", Decimal("800.00")),
        ("Syphilis (VDRL / TPHA)", "ICT", "Non-Reactive", Decimal("500.00")),
        ("Typhidot (IgM / IgG)", "ICT", "IgM: Negative | IgG: Negative", Decimal("800.00")),
        ("Widal / Typhoid Agglutination", "Titre", "TO: < 1:80 | TH: < 1:80 (Negative)", Decimal("600.00")),
        ("Dengue NS1 Antigen", "ICT", "Negative", Decimal("1200.00")),
        ("Dengue IgM & IgG Antibodies", "ICT", "IgM: Negative | IgG: Negative", Decimal("1200.00")),
        ("Malaria Parasite (MP Slide / ICT)", "—", "Negative (No parasite seen)", Decimal("400.00")),
        ("H. Pylori (Serum / Stool Antigen)", "ICT", "Negative", Decimal("900.00")),
        ("CRP (C-Reactive Protein)", "mg/L", "< 6.0 mg/L (Normal)", Decimal("600.00")),
        ("RA Factor (Rheumatoid Factor)", "IU/mL", "< 14 IU/mL (Negative)", Decimal("600.00")),
        ("ASO Titre", "IU/mL", "< 200 IU/mL", Decimal("700.00")),
    ],
    "Hormones & Endocrinology": [
        ("TSH (Thyroid Stimulating Hormone)", "uIU/mL", "0.4–4.2 uIU/mL", Decimal("1200.00")),
        ("Free T3 (FT3)", "pg/mL", "2.0–4.4 pg/mL", Decimal("1200.00")),
        ("Free T4 (FT4)", "ng/dL", "0.93–1.7 ng/dL", Decimal("1200.00")),
        ("Serum Beta-hCG (Pregnancy Quantitative)", "mIU/mL", "Non-Pregnant: < 5 mIU/mL", Decimal("1400.00")),
        ("Serum Prolactin", "ng/mL", "Male: 2–18 | Female: 3–29", Decimal("1400.00")),
        ("Serum Ferritin", "ng/mL", "Male: 20–250 | Female: 10–120", Decimal("1500.00")),
        ("Vitamin D3 (25-Hydroxy)", "ng/mL", "Deficient: <20 | Sufficient: 30–100", Decimal("2800.00")),
        ("Vitamin B12", "pg/mL", "200–900 pg/mL", Decimal("2200.00")),
    ],
    "Urinalysis & Body Fluids": [
        ("Urine Routine Examination (Urine R/E)", "Physical/Micro", "Pus Cells: 1–2 /HPF | RBCs: Nil | Albumin: Nil | Sugar: Nil", Decimal("400.00")),
        ("Urine Pregnancy Test (Strip)", "—", "Negative / Positive", Decimal("200.00")),
        ("Urine 24-Hour Total Protein", "mg/24hr", "< 150 mg/24 hours", Decimal("800.00")),
        ("Urine Culture & Sensitivity (C/S)", "—", "No Bacterial Growth After 48 Hrs", Decimal("1200.00")),
        ("Stool Routine Examination (Stool R/E)", "Physical/Micro", "No Ova, Cysts or Parasites Seen", Decimal("400.00")),
        ("Stool Occult Blood (OB)", "—", "Negative", Decimal("500.00")),
        ("Semen Analysis (Complete)", "Routine", "Count: >15 M/mL | Motility: >40%", Decimal("1000.00")),
    ],
}

# Standard Radiology, Ultrasound, X-Rays, ECG, CT & MRI Scans
IMAGING_CATALOG = [
    # --- Ultrasound ---
    ("ULTRASOUND", "Ultrasound Abdomen & Pelvis", Decimal("1500.00")),
    ("ULTRASOUND", "Ultrasound Whole Abdomen", Decimal("1200.00")),
    ("ULTRASOUND", "Ultrasound Pelvis (Gynae)", Decimal("1000.00")),
    ("ULTRASOUND", "Obstetric Ultrasound (Routine Pregnancy)", Decimal("1200.00")),
    ("ULTRASOUND", "Obstetric Color Doppler / Anomaly Scan", Decimal("2500.00")),
    ("ULTRASOUND", "Ultrasound KUB (Kidneys, Ureters, Bladder)", Decimal("1200.00")),
    ("ULTRASOUND", "Ultrasound Scrotum & Testicular Doppler", Decimal("1800.00")),
    ("ULTRASOUND", "Ultrasound Thyroid / Neck", Decimal("1500.00")),
    ("ULTRASOUND", "Ultrasound Breast (Bilateral)", Decimal("1800.00")),
    ("ULTRASOUND", "Transvaginal Ultrasound (TVS)", Decimal("2000.00")),
    ("ULTRASOUND", "Ultrasound Guided Aspiration / Procedure", Decimal("3500.00")),

    # --- X-Ray ---
    ("XRAY", "Chest X-Ray PA View", Decimal("700.00")),
    ("XRAY", "Chest X-Ray AP View (Portable / Bedside)", Decimal("900.00")),
    ("XRAY", "Abdomen X-Ray Erect & Supine (KUB)", Decimal("1200.00")),
    ("XRAY", "Cervical Spine X-Ray AP & Lateral", Decimal("1000.00")),
    ("XRAY", "Lumbo-Sacral Spine (LS Spine) AP & Lateral", Decimal("1000.00")),
    ("XRAY", "Dorsal Spine X-Ray AP & Lateral", Decimal("1000.00")),
    ("XRAY", "Knee Joint X-Ray AP & Lateral (Single / Both)", Decimal("900.00")),
    ("XRAY", "Pelvis X-Ray AP View", Decimal("800.00")),
    ("XRAY", "Skull X-Ray AP & Lateral", Decimal("1000.00")),
    ("XRAY", "PNS X-Ray (Water's View - Sinuses)", Decimal("700.00")),
    ("XRAY", "Foot & Ankle X-Ray AP & Lateral", Decimal("800.00")),
    ("XRAY", "Hand & Wrist X-Ray AP & Lateral", Decimal("800.00")),
    ("XRAY", "Shoulder Joint X-Ray AP", Decimal("700.00")),

    # --- Cardiology & Physiological Scans ---
    ("ECG", "12-Lead ECG (Electrocardiogram)", Decimal("500.00")),
    ("ECHO", "2D Echocardiography & Color Doppler", Decimal("3500.00")),

    # --- Advanced Imaging (CT / MRI / Mammography) ---
    ("CT", "CT Brain (Plain)", Decimal("6500.00")),
    ("CT", "CT Brain (Contrast)", Decimal("9000.00")),
    ("CT", "CT Chest (HRCT)", Decimal("8500.00")),
    ("CT", "CT Abdomen & Pelvis (Triple Phase)", Decimal("14000.00")),
    ("MRI", "MRI Brain (Plain)", Decimal("11000.00")),
    ("MRI", "MRI Lumbo-Sacral Spine", Decimal("11000.00")),
    ("MAMMO", "Digital Mammography (Bilateral Breast)", Decimal("3000.00")),
]


@transaction.atomic
def seed_hospital_catalogs(hospital=None):
    """Module-aware seeder: Populates Lab Tests only if 'lab' is enabled,
    and Radiology/Scans only if 'imaging' is enabled for this hospital.
    For hospital-less desktop/LAN installation (hospital=None), seeds both.
    """
    from lab.models import TestCategory, LabTest, TestOrder
    from imaging.models import ScanType, ImagingStudy

    stats = {
        "categories_created": 0,
        "lab_tests_created": 0,
        "scans_created": 0,
    }

    enabled = getattr(hospital, 'enabled_modules', None)
    # Check module permissions for this tenant
    seed_lab = hospital is None or (enabled and 'lab' in enabled)
    seed_imaging = hospital is None or (enabled and 'imaging' in enabled)

    # 1. Seed Comprehensive Lab Tests (ONLY if 'lab' is enabled)
    if seed_lab:
        for cat_name, tests in LAB_CATALOG.items():
            cat, cat_created = TestCategory.all_objects.get_or_create(
                name=cat_name,
                hospital=hospital
            )
            if cat_created:
                stats["categories_created"] += 1

            for test_name, unit, normal_range, default_price in tests:
                _, test_created = LabTest.all_objects.get_or_create(
                    category=cat,
                    name=test_name,
                    hospital=hospital,
                    defaults={
                        "unit": unit or "",
                        "normal_range": normal_range or "",
                        "price": default_price,
                        "cost_price": Decimal("0.00"),
                    }
                )
                if test_created:
                    stats["lab_tests_created"] += 1
    elif hospital is not None:
        # If 'lab' is disabled and no orders exist, clean up any unneeded rows to save space
        if not TestOrder.objects.filter(patient__hospital=hospital).exists():
            LabTest.all_objects.filter(hospital=hospital).delete()
            TestCategory.all_objects.filter(hospital=hospital).delete()

    # 2. Seed Comprehensive Imaging & Radiology Scans (ONLY if 'imaging' is enabled)
    if seed_imaging:
        for modality, scan_name, default_price in IMAGING_CATALOG:
            _, scan_created = ScanType.all_objects.get_or_create(
                modality=modality,
                name=scan_name,
                hospital=hospital,
                defaults={
                    "price": default_price,
                    "cost_price": Decimal("0.00"),
                    "is_active": True,
                }
            )
            if scan_created:
                stats["scans_created"] += 1
    elif hospital is not None:
        # If 'imaging' is disabled and no studies exist, clean up unneeded scan types
        if not ImagingStudy.objects.filter(patient__hospital=hospital).exists():
            ScanType.all_objects.filter(hospital=hospital).delete()

    return stats
