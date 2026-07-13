from django.core.management.base import BaseCommand
from django.db import transaction
from lab.models import TestCategory, LabTest, Patient

CATEGORIES_AND_TESTS = {
    "Hematology": [
        ("Hemoglobin", "g/dL", "M: 13.5–17.5 | F: 12.0–15.5"),
        ("WBC Count", "10^9/L", "4.0–11.0"),
        ("Platelet Count", "10^9/L", "150–450"),
        ("Hematocrit (PCV)", "%", "M: 41–53 | F: 36–46"),
        ("RBC Count", "10^12/L", "M: 4.5–5.9 | F: 4.1–5.1"),
        ("MCV", "fL", "80–100"),
    ],
    "Biochemistry": [
        ("Fasting Blood Glucose", "mg/dL", "70–100"),
        ("Random Blood Glucose", "mg/dL", "< 140 (2h post-meal < 180)"),
        ("HbA1c", "%", "4.0–5.6"),
        ("Blood Urea", "mg/dL", "15–40"),
        ("Serum Creatinine", "mg/dL", "M: 0.7–1.3 | F: 0.6–1.1"),
        ("Uric Acid", "mg/dL", "M: 3.4–7.0 | F: 2.4–6.0"),
        ("Total Cholesterol", "mg/dL", "< 200"),
        ("Triglycerides", "mg/dL", "< 150"),
        ("HDL Cholesterol", "mg/dL", "M: > 40 | F: > 50"),
        ("LDL Cholesterol (Calc.)", "mg/dL", "< 100 (optimal)"),
        ("AST (SGOT)", "U/L", "0–40"),
        ("ALT (SGPT)", "U/L", "0–41"),
        ("Alkaline Phosphatase (ALP)", "U/L", "44–147"),
        ("Total Bilirubin", "mg/dL", "0.3–1.2"),
        ("Sodium (Na+)", "mmol/L", "135–145"),
        ("Potassium (K+)", "mmol/L", "3.5–5.0"),
        ("Chloride (Cl-)", "mmol/L", "98–106"),
    ],
    "Immunology / Serology": [
        ("HBsAg", "", "Non-Reactive"),
        ("Anti-HCV", "", "Non-Reactive"),
        ("HIV 1/2", "", "Non-Reactive"),
        ("CRP (C-Reactive Protein)", "mg/L", "< 5"),
        ("Rheumatoid Factor (RF)", "IU/mL", "< 14"),
        ("Dengue NS1 Antigen", "", "Negative"),
        ("Typhidot (IgM/IgG)", "", "Negative"),
        ("HCG (Pregnancy Test)", "", "Negative"),
    ],
    "Coagulation": [
        ("Prothrombin Time (PT)", "sec", "11–13.5"),
        ("INR", "", "0.8–1.2"),
        ("APTT", "sec", "25–35"),
    ],
    "Urinalysis": [
        ("Urine R/E (Routine Exam)", "", "Clear, pH 4.6–8.0, SG 1.005–1.030"),
        ("Urine Albumin (Qual.)", "", "Negative"),
        ("Urine Sugar (Qual.)", "", "Negative"),
    ],
    "Hormones": [
        ("TSH (Thyroid Stimulating Hormone)", "µIU/mL", "0.4–4.0"),
        ("Free T4", "ng/dL", "0.8–1.8"),
    ],
    "Microbiology": [
        ("Urine Culture & Sensitivity", "", "No Growth"),
        ("Sputum AFB (Microscopy)", "", "Negative"),
        ("Throat Swab Culture", "", "No Pathogen Isolated"),
    ],
}

SAMPLE_PATIENTS = [
    {"name": "Ali Khan", "age": 32, "gender": "M", "phone": "0300-1111111", "address": "Karachi"},
    {"name": "Ayesha Bibi", "age": 27, "gender": "F", "phone": "0312-2222222", "address": "Lahore"},
    {"name": "Bilal Ahmed", "age": 45, "gender": "M", "phone": "0333-3333333", "address": "Islamabad"},
]

class Command(BaseCommand):
    help = "Seeds essential Lab data: categories, tests (with units & ranges), and sample patients."

    @transaction.atomic
    def handle(self, *args, **options):
        created_cat = 0
        created_tests = 0
        created_patients = 0

        for cat_name, tests in CATEGORIES_AND_TESTS.items():
            cat, cat_created = TestCategory.objects.get_or_create(name=cat_name)
            if cat_created:
                created_cat += 1

            for test_name, unit, normal_range in tests:
                _, t_created = LabTest.objects.get_or_create(
                    category=cat,
                    name=test_name,
                    defaults={"unit": unit or None, "normal_range": normal_range or None, "price": 0},
                )
                if t_created:
                    created_tests += 1

        for p in SAMPLE_PATIENTS:
            _, p_created = Patient.objects.get_or_create(
                name=p["name"],
                defaults={
                    "age": p["age"],
                    "gender": p["gender"],
                    "phone": p["phone"],
                    "address": p["address"],
                },
            )
            if p_created:
                created_patients += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seed complete ✅  Categories: +{created_cat}, Tests: +{created_tests}, Patients: +{created_patients}"
        ))
