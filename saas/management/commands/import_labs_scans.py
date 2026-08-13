from django.core.management.base import BaseCommand
from decimal import Decimal
from lab.models import TestCategory, LabTest
from imaging.models import ScanType

class Command(BaseCommand):
    help = ("Import standard diagnostic lab tests and scan types into every "
            "hospital's catalogue (or one named with --hospital). Idempotent.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--hospital', dest='slug', default=None,
            help="Import into only this hospital's catalogue (slug).")

    def handle(self, *args, **options):
        from saas.models import Hospital

        # Both catalogues are per-hospital (see lab.models.LabTest). A command
        # binds no tenant, so rows have to be stamped explicitly or they land
        # under `hospital = NULL` where no hosted tenant can see them. With no
        # hospitals at all — a fresh install or the desktop build — NULL is
        # correct, hence the [None] fallback.
        if options['slug']:
            targets = list(Hospital.objects.filter(slug=options['slug']))
            if not targets:
                self.stderr.write(f"No hospital with slug '{options['slug']}'.")
                return
        else:
            targets = list(Hospital.objects.all()) or [None]

        # 1. Categories and Lab Tests
        lab_data = {
            "Hematology": [
                {"name": "Complete Blood Count (CBC)", "unit": "g/dL", "normal_range": "12.0 - 16.0", "price": "450.00"},
                {"name": "Hemoglobin (Hb)", "unit": "g/dL", "normal_range": "12.0 - 16.0", "price": "250.00"},
                {"name": "Blood Grouping & Rh Factor", "unit": "", "normal_range": "", "price": "200.00"},
                {"name": "Erythrocyte Sedimentation Rate (ESR)", "unit": "mm/1st hr", "normal_range": "0 - 15", "price": "250.00"},
            ],
            "Biochemistry": [
                {"name": "Blood Glucose - Random (RBS)", "unit": "mg/dL", "normal_range": "70 - 140", "price": "150.00"},
                {"name": "Blood Glucose - Fasting (FBS)", "unit": "mg/dL", "normal_range": "70 - 110", "price": "150.00"},
                {"name": "HbA1c (Glycated Hemoglobin)", "unit": "%", "normal_range": "4.0 - 5.6", "price": "900.00"},
                {"name": "Liver Function Test (LFT)", "unit": "", "normal_range": "", "price": "1200.00"},
                {"name": "Renal Function Test (RFT)", "unit": "", "normal_range": "", "price": "900.00"},
                {"name": "Lipid Profile", "unit": "", "normal_range": "", "price": "1200.00"},
                {"name": "Serum Uric Acid", "unit": "mg/dL", "normal_range": "2.4 - 7.0", "price": "350.00"},
                {"name": "Serum Creatinine", "unit": "mg/dL", "normal_range": "0.6 - 1.2", "price": "300.00"},
                {"name": "Serum Urea", "unit": "mg/dL", "normal_range": "15 - 45", "price": "300.00"},
            ],
            "Serology": [
                {"name": "HBsAg (Hepatitis B)", "unit": "", "normal_range": "Negative", "price": "400.00"},
                {"name": "Anti-HCV (Hepatitis C)", "unit": "", "normal_range": "Negative", "price": "500.00"},
                {"name": "Typhidot (IgM / IgG)", "unit": "", "normal_range": "Negative", "price": "600.00"},
                {"name": "Dengue NS1 Antigen", "unit": "", "normal_range": "Negative", "price": "1200.00"},
                {"name": "Widal Test", "unit": "", "normal_range": "", "price": "350.00"},
                {"name": "Malaria ICT", "unit": "", "normal_range": "Negative", "price": "400.00"},
            ],
            "Urine Analysis": [
                {"name": "Urine Routine Examination (R/E)", "unit": "", "normal_range": "", "price": "250.00"},
                {"name": "Urine Pregnancy Test (hCG)", "unit": "", "normal_range": "Negative", "price": "250.00"},
            ]
        }

        for hospital in targets:
            for cat_name, tests in lab_data.items():
                category, _ = TestCategory.all_objects.get_or_create(
                    name=cat_name, hospital=hospital)
                for t in tests:
                    test, created = LabTest.all_objects.get_or_create(
                        category=category,
                        name=t["name"],
                        hospital=hospital,
                        defaults={
                            "unit": t["unit"],
                            "normal_range": t["normal_range"],
                            "price": Decimal(t["price"])
                        }
                    )
                    if created:
                        self.stdout.write(f"Created LabTest: {test.name}")

        # 2. Scan Types
        scan_data = [
            # Ultrasound
            {"modality": "ULTRASOUND", "name": "Ultrasound Abdomen & Pelvis", "price": "1500.00"},
            {"modality": "ULTRASOUND", "name": "Ultrasound KUB", "price": "1200.00"},
            {"modality": "ULTRASOUND", "name": "Ultrasound Obstetric (Pregnancy)", "price": "1000.00"},
            {"modality": "ULTRASOUND", "name": "Ultrasound Pelvis", "price": "1000.00"},
            # X-Ray
            {"modality": "XRAY", "name": "X-Ray Chest PA View", "price": "600.00"},
            {"modality": "XRAY", "name": "X-Ray Knee AP/LAT View", "price": "700.00"},
            {"modality": "XRAY", "name": "X-Ray Spine AP/LAT View", "price": "900.00"},
            # CT
            {"modality": "CT", "name": "CT Scan Brain (Plain)", "price": "4500.00"},
            {"modality": "CT", "name": "CT Scan Abdomen (Plain)", "price": "6500.00"},
            # MRI
            {"modality": "MRI", "name": "MRI Brain", "price": "8500.00"},
            {"modality": "MRI", "name": "MRI Spine", "price": "8500.00"},
            # ECG & Echo
            {"modality": "ECG", "name": "ECG (12-Lead)", "price": "400.00"},
            {"modality": "ECHO", "name": "Echocardiography", "price": "2500.00"},
        ]

        for hospital in targets:
            for s in scan_data:
                scan, created = ScanType.all_objects.get_or_create(
                    modality=s["modality"],
                    name=s["name"],
                    hospital=hospital,
                    defaults={
                        "price": Decimal(s["price"]),
                        "is_active": True
                    }
                )
                if created:
                    self.stdout.write(f"Created ScanType: {scan.name}")

        self.stdout.write("Lab tests and Scans imported successfully!")
