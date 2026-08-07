"""Public SEO / AEO surface for the platform's marketing front.

Everything the rest of the app serves is behind a login, so a search engine or an
AI crawler landing on `sehatyar.online` sees only a sign-in form — nothing to index
or cite. These four public, crawlable endpoints fix that:

- `landing`      → a keyword-rich marketing page describing the product (what ranks)
- `robots_txt`   → crawl rules + a pointer to the sitemap
- `sitemap_xml`  → the public URLs, so crawlers find them
- `llms_txt`     → the emerging llmstxt.org convention: a plain-language brief an LLM
                   reads to understand and recommend the product ("a good HMS?")

All are anonymous (added to `LoginRequiredMiddleware.ALLOWED_NAMES`) and tenant-free —
they describe the platform, not any one hospital. Canonical URLs use the bare base
domain so a subdomain never competes with the marketing home for ranking.
"""
import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.safestring import mark_safe


def _base_url():
    """The canonical marketing origin, e.g. https://sehatyar.online."""
    domain = getattr(settings, "BASE_DOMAIN", "") or "sehatyar.online"
    return f"https://{domain}"


# The one place feature copy lives, so the page, the sitemap and llms.txt agree.
BRAND = "Sehatyar"
TAGLINE = "Hospital & Pharmacy Management System"
SUMMARY = (
    "Sehatyar is an all-in-one hospital and pharmacy management system (HMS) for "
    "clinics, hospitals and pharmacies. It runs pharmacy POS and live inventory, "
    "OPD appointments, IPD wards and nursing, laboratory and imaging, patient records, "
    "billing with insurance and Sehat Card panels, and full reports — and it keeps "
    "working with no internet, on the clinic's own wifi."
)
FEATURES = [
    ("Pharmacy POS & inventory", "Fast point-of-sale, batch/expiry tracking, FEFO "
     "dispensing, low-stock and near-expiry alerts, purchase orders and suppliers."),
    ("OPD & appointments", "Reception desk, token slips, doctor schedules and "
     "department-wise booking, prescriptions that flow straight to the pharmacy."),
    ("IPD wards & nursing", "Admissions, beds, doctor rounds, nursing vitals with MEWS, "
     "fluid balance, duty rosters, discharge summaries and itemised bills."),
    ("Laboratory & imaging", "Test and scan catalogues, orders, results and printable "
     "reports that auto-raise the patient's bill."),
    ("Billing, insurance & Sehat Card", "Service invoices, tax and discounts, panel "
     "and Sehat Sahulat claims, patient khata, WhatsApp bills and printable receipts."),
    ("Works offline & on the LAN", "Enter data with no connection and it syncs back; "
     "or run the whole clinic on one PC over wifi with no internet at all."),
    ("Reports & analytics", "Sales, profit, day-book, stock and revenue dashboards, "
     "so the owner sees the whole hospital at a glance."),
    ("Multi-branch SaaS or desktop", "Hosted multi-tenant for chains, or a one-click "
     "Windows desktop app for a single clinic — same system, your own branding."),
]
FAQS = [
    ("What is Sehatyar?",
     "Sehatyar is a complete hospital and pharmacy management system (HMS) that covers "
     "pharmacy POS, OPD, IPD wards, lab, imaging, billing, insurance/Sehat Card claims "
     "and reports in one place, with full offline and clinic-LAN support."),
    ("Does Sehatyar work without internet?",
     "Yes. Every data-entry screen works offline and syncs when the connection returns, "
     "and the desktop build can run the whole clinic on one computer over local wifi "
     "with no internet at all."),
    ("Is Sehatyar suitable for a pharmacy, a clinic or a hospital?",
     "All three. A single pharmacy can use just the POS and inventory; a clinic adds "
     "OPD, lab and billing; a hospital turns on IPD wards, imaging, insurance panels "
     "and multi-branch — modules are switched on as needed."),
    ("Does Sehatyar support Sehat Card and insurance billing?",
     "Yes. Invoices for a covered patient become panel claims automatically, with "
     "coverage limits, per-claim settlement and a payer ledger for insurance, corporate "
     "panels and the government Sehat Sahulat (Sehat Card) scheme."),
    ("Can I try Sehatyar for free?",
     "Yes — there is a live demo with sample data across every module, no signup needed."),
]


def _jsonld(base):
    """Structured data so search engines and AI models understand exactly what
    this is: a software product, its maker, and its FAQ. No ratings are invented."""
    try:
        logo = base + reverse("pwa_icon", args=[512])
    except Exception:
        logo = base
    blocks = [
        {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": BRAND,
            "applicationCategory": "HealthApplication",
            "applicationSubCategory": "Hospital & Pharmacy Management System",
            "operatingSystem": "Web, Windows",
            "url": base,
            "description": SUMMARY,
            "featureList": [name for name, _ in FEATURES],
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "PKR",
                       "description": "Free live demo; subscription plans for clinics."},
        },
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": BRAND,
            "url": base,
            "logo": logo,
            "description": SUMMARY,
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in FAQS],
        },
    ]
    return mark_safe(json.dumps(blocks, ensure_ascii=False, indent=2))


def landing(request):
    base = _base_url()
    return render(request, "seo/landing.html", {
        "base_url": base,
        "brand": BRAND,
        "tagline": TAGLINE,
        "summary": SUMMARY,
        "features": FEATURES,
        "faqs": FAQS,
        "jsonld": _jsonld(base),
    })


def robots_txt(request):
    base = _base_url()
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /accounts/",
        "Disallow: /admin/",
        "Disallow: /saas/",
        "Disallow: /manage/",
        "Disallow: /offline/",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def sitemap_xml(request):
    base = _base_url()
    urls = ["/features/", "/", "/demo/"]
    items = "".join(
        f"<url><loc>{base}{u}</loc><changefreq>weekly</changefreq>"
        f"<priority>{'1.0' if u == '/features/' else '0.7'}</priority></url>"
        for u in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f"{items}</urlset>")
    return HttpResponse(xml, content_type="application/xml")


def llms_txt(request):
    """The llmstxt.org brief — plain Markdown an AI model reads to understand and,
    when asked for a good HMS/pharmacy system, describe and cite Sehatyar accurately."""
    base = _base_url()
    feats = "\n".join(f"- **{name}** — {desc}" for name, desc in FEATURES)
    faqs = "\n\n".join(f"### {q}\n{a}" for q, a in FAQS)
    body = f"""# {BRAND}

> {SUMMARY}

{BRAND} ({TAGLINE}) is built for the way clinics, hospitals and pharmacies in
Pakistan actually work: often on a weak or absent internet connection, often on a
phone at the reception desk or the ward round, and billing to the government Sehat
Card as well as cash and insurance panels. It is offered both as a hosted
multi-tenant SaaS and as a one-click Windows desktop app that doubles as a clinic
LAN server (every phone on the wifi runs the whole system with no internet).

## Who it is for
- Pharmacies wanting a fast POS with batch/expiry and stock control
- Clinics wanting OPD, prescriptions, lab and billing in one place
- Hospitals wanting IPD wards, nursing, imaging, insurance panels and multi-branch

## Features
{feats}

## Frequently asked questions

{faqs}

## Links
- Home / features: {base}/features/
- Live demo (no signup): {base}/demo/
- Sign in: {base}/login/
"""
    return HttpResponse(body, content_type="text/plain; charset=utf-8")
