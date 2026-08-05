"""Seed a starter set of common ICD-10 codes (global catalogue). Idempotent."""
from django.core.management.base import BaseCommand

from diagnosis.models import DiagnosisCode

CODES = [
    ('A09', 'Infectious gastroenteritis / diarrhoea', 'Infectious'),
    ('A15.0', 'Tuberculosis of lung', 'Infectious'),
    ('B50.9', 'Plasmodium falciparum malaria, unspecified', 'Infectious'),
    ('B54', 'Unspecified malaria', 'Infectious'),
    ('A01.0', 'Typhoid fever', 'Infectious'),
    ('E11.9', 'Type 2 diabetes mellitus, without complications', 'Endocrine'),
    ('E10.9', 'Type 1 diabetes mellitus, without complications', 'Endocrine'),
    ('E66.9', 'Obesity, unspecified', 'Endocrine'),
    ('D50.9', 'Iron deficiency anaemia, unspecified', 'Blood'),
    ('I10', 'Essential (primary) hypertension', 'Circulatory'),
    ('I20.9', 'Angina pectoris, unspecified', 'Circulatory'),
    ('I21.9', 'Acute myocardial infarction, unspecified', 'Circulatory'),
    ('I50.9', 'Heart failure, unspecified', 'Circulatory'),
    ('I63.9', 'Cerebral infarction (stroke), unspecified', 'Circulatory'),
    ('J00', 'Acute nasopharyngitis (common cold)', 'Respiratory'),
    ('J02.9', 'Acute pharyngitis, unspecified', 'Respiratory'),
    ('J18.9', 'Pneumonia, unspecified organism', 'Respiratory'),
    ('J45.9', 'Asthma, unspecified', 'Respiratory'),
    ('J44.9', 'Chronic obstructive pulmonary disease, unspecified', 'Respiratory'),
    ('K29.7', 'Gastritis, unspecified', 'Digestive'),
    ('K30', 'Functional dyspepsia', 'Digestive'),
    ('K52.9', 'Noninfective gastroenteritis and colitis', 'Digestive'),
    ('N39.0', 'Urinary tract infection, site not specified', 'Genitourinary'),
    ('N18.9', 'Chronic kidney disease, unspecified', 'Genitourinary'),
    ('O80', 'Single spontaneous delivery', 'Pregnancy'),
    ('O82', 'Single delivery by caesarean section', 'Pregnancy'),
    ('Z34.9', 'Supervision of normal pregnancy, unspecified', 'Pregnancy'),
    ('R50.9', 'Fever, unspecified', 'Symptoms'),
    ('R51', 'Headache', 'Symptoms'),
    ('R10.4', 'Abdominal pain, unspecified', 'Symptoms'),
    ('M54.5', 'Low back pain', 'Musculoskeletal'),
    ('S00.9', 'Superficial injury of head', 'Injury'),
    ('T14.9', 'Injury, unspecified', 'Injury'),
    ('F32.9', 'Depressive episode, unspecified', 'Mental'),
    ('F41.9', 'Anxiety disorder, unspecified', 'Mental'),
]


class Command(BaseCommand):
    help = 'Seed common ICD-10 diagnosis codes (global catalogue).'

    def handle(self, *args, **options):
        created = 0
        for code, title, category in CODES:
            _, made = DiagnosisCode.objects.get_or_create(
                code=code, defaults={'title': title, 'category': category})
            created += 1 if made else 0
        self.stdout.write(self.style.SUCCESS(
            f'ICD-10 seed done: {created} added, {len(CODES) - created} already present.'))
