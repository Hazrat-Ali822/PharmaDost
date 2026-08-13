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


# Keyword-targeted content pages. Each ranks for its own search and gives an AI
# answer engine a focused, factual page to cite. Slugs are top-level URLs, so they
# are registered in pharma_mgmt/urls.py ABOVE the <slug:hospital_slug> catch-all.
# `sections` is a list of (heading, [paragraphs]); copy is genuine, not stuffed.
CONTENT_PAGES = {
    "hospital-management-system": {
        "nav": "Hospital Management System",
        "h1": "Hospital Management System (HMS) for Pakistan",
        "meta": ("Sehatyar is a complete hospital management system for Pakistani "
                 "hospitals and clinics — OPD, IPD wards, pharmacy, lab, imaging, "
                 "billing and Sehat Card/insurance — online or fully offline on the "
                 "clinic LAN."),
        "keywords": ("hospital management system, HMS, hospital software Pakistan, "
                     "hospital management software, HMIS, hospital ERP, OPD IPD software"),
        "lede": ("A hospital management system (HMS) runs a hospital's day-to-day work "
                 "— patients, doctors, wards, pharmacy, lab, billing and reports — from "
                 "one place. Sehatyar is an HMS built for how hospitals in Pakistan "
                 "actually run: on a weak or absent internet connection, on phones at "
                 "the desk and the ward, and billing to cash, insurance and the Sehat "
                 "Card alike."),
        "sections": [
            ("What a hospital management system does", [
                "An HMS replaces the registers, spreadsheets and disconnected apps a "
                "hospital juggles with a single record. Reception registers a patient "
                "once; that record then follows them through OPD, the ward, the lab and "
                "the pharmacy, and every charge lands on one bill.",
                "Sehatyar covers the whole chain: patient registration and MRN, OPD "
                "appointments and tokens, IPD admissions with beds, doctor rounds and "
                "nursing vitals, laboratory and imaging orders, pharmacy dispensing with "
                "live stock, and billing with tax, discounts, insurance panels and "
                "printable receipts."]),
            ("Why Sehatyar fits Pakistani hospitals", [
                "It works with no internet. Every screen can be filled offline and syncs "
                "back later, and the desktop build turns one PC into a clinic server so "
                "every phone on the wifi runs the whole system with no internet at all.",
                "It bills the Sehat Card. Invoices for a covered patient become panel "
                "claims automatically, with coverage limits and a payer ledger for the "
                "government Sehat Sahulat scheme and private insurance. It is affordable, "
                "installs as a Windows app or runs in the browser, and each hospital "
                "keeps its own name, logo and colours."]),
        ],
        "faqs": [
            ("What is a hospital management system?",
             "A hospital management system (HMS) is software that runs a hospital's "
             "clinical and administrative work — patient records, OPD, IPD wards, "
             "pharmacy, lab, imaging, billing and reports — from one connected system."),
            ("Which is the best hospital management system for a clinic in Pakistan?",
             "Sehatyar is built specifically for Pakistani clinics and hospitals: it "
             "works fully offline and on the clinic LAN, bills the Sehat Card and "
             "insurance panels, and runs on affordable hardware with per-hospital "
             "branding — with a free live demo to try first."),
            ("Does the hospital system work without internet?",
             "Yes — Sehatyar works offline on every screen and can run the whole clinic "
             "on one computer over local wifi with no internet at all."),
        ],
    },
    "pharmacy-management-software": {
        "nav": "Pharmacy Software",
        "h1": "Pharmacy Management Software & POS",
        "meta": ("Sehatyar pharmacy software: a fast POS with batch and expiry "
                 "tracking, FEFO dispensing, low-stock alerts, supplier purchase "
                 "orders and profit reports — for pharmacies and hospital pharmacies "
                 "in Pakistan, online or offline."),
        "keywords": ("pharmacy management software, pharmacy POS, medical store "
                     "software, pharmacy billing software Pakistan, medicine inventory "
                     "software, chemist software"),
        "lede": ("Sehatyar's pharmacy module is a fast point-of-sale backed by real "
                 "inventory: it tracks every batch and expiry, dispenses oldest-first "
                 "so nothing expires on the shelf, and warns before stock runs out — "
                 "for a standalone pharmacy or a hospital pharmacy."),
        "sections": [
            ("Sell fast, and never oversell", [
                "The POS is built for a busy counter: search a medicine, add it, take "
                "payment, print. Stock is checked live, so you sell only what is on hand "
                "and in date, and the sale freezes its cost so the profit report is true.",
                "Batch and expiry are tracked per medicine, dispensing is FEFO (first "
                "expiry, first out), and near-expiry and low-stock alerts fire before a "
                "problem starts. Returns quarantine expired stock automatically."]),
            ("Purchasing, suppliers and profit", [
                "Reorder suggestions are built from real sales velocity and turn into "
                "draft purchase orders grouped by supplier — send them, then receive "
                "stock straight back in. Supplier khata and payments are tracked.",
                "It bills panels and the Sehat Card too, prints or WhatsApps the bill, "
                "and works offline — a dropped connection never stops the counter."]),
        ],
        "faqs": [
            ("What is pharmacy management software?",
             "Pharmacy management software runs a pharmacy's selling and stock: "
             "point-of-sale billing, batch and expiry tracking, purchasing from "
             "suppliers, and profit and inventory reports."),
            ("Does the pharmacy POS track batches and expiry?",
             "Yes — Sehatyar tracks every batch and its expiry, dispenses first-expiry-"
             "first, and blocks expired stock from being sold, with near-expiry alerts."),
            ("Can the pharmacy software work offline?",
             "Yes — the counter keeps working with no internet; offline sales are "
             "recorded and reconciled, and the desktop build needs no internet at all."),
        ],
    },
    "sehat-card-billing-software": {
        "nav": "Sehat Card Billing",
        "h1": "Sehat Card & Insurance Billing Software",
        "meta": ("Sehatyar bills the government Sehat Card (Sehat Sahulat), private "
                 "insurance and corporate panels: automatic claims, coverage limits, "
                 "per-claim settlement and a payer ledger — inside a full hospital and "
                 "pharmacy system."),
        "keywords": ("Sehat Card billing software, Sehat Sahulat software, panel "
                     "billing, insurance billing software Pakistan, IPD claims, "
                     "empanelled hospital software"),
        "lede": ("Sehatyar handles institutional payers — the government Sehat Card "
                 "(Sehat Sahulat), private insurance and corporate panels — as a proper "
                 "receivables ledger, so a covered patient's bills become claims with no "
                 "extra step."),
        "sections": [
            ("Claims that build themselves", [
                "Link a patient to their panel and card number once. From then on every "
                "bill — OPD, lab, imaging, IPD discharge — is attributed to the panel "
                "automatically and stamped as a pending claim; the co-pay collected at "
                "the counter is recorded separately.",
                "Each panel can cover only certain services (OPD-only, the inpatient "
                "package, or the whole hospital), and a per-patient coverage limit (like "
                "a Sehat Card annual cap) is enforced — anything over the limit is billed "
                "to the patient as normal."]),
            ("Settlement and the payer ledger", [
                "What the panel owes is computed, never hand-kept, so it cannot drift. "
                "Payments from a panel are allocated across its open claims oldest-first, "
                "each claim shows its settled amount and status, and the ledger prints a "
                "clean statement for reconciliation with the payer."]),
        ],
        "faqs": [
            ("Can Sehatyar bill the Sehat Card (Sehat Sahulat)?",
             "Yes — a Sehat Card patient's invoices become panel claims automatically, "
             "with coverage limits, per-claim settlement and a payer ledger for the "
             "government Sehat Sahulat scheme."),
            ("Does it also handle private insurance and company panels?",
             "Yes — private insurance, corporate panels and the Sehat Card are all "
             "handled the same way, each with its own covered services and ledger."),
            ("How is the co-pay handled?",
             "The co-pay collected from the patient at the counter is recorded "
             "separately; the panel is billed only the balance it is responsible for."),
        ],
    },
    "clinic-management-software": {
        "nav": "Clinic Software",
        "h1": "Clinic Management Software",
        "meta": ("Sehatyar clinic software: reception and OPD appointments, doctor "
                 "schedules, prescriptions, lab and imaging, and patient billing in one "
                 "flow — works offline and on the clinic LAN, with a free demo."),
        "keywords": ("clinic management software, clinic software, OPD software, doctor "
                     "appointment software, patient management software Pakistan, "
                     "EMR software"),
        "lede": ("Sehatyar runs a clinic from the front desk to the bill: reception "
                 "registers or finds the patient, books the doctor, and the visit flows "
                 "through consultation, prescription, lab and billing without re-typing "
                 "anything."),
        "sections": [
            ("From the front desk to the prescription", [
                "Reception picks a department and an available doctor, registers a new "
                "patient or finds an old one by name, mobile, CNIC or MRN, and prints a "
                "token slip. The doctor writes the prescription, which flows to the "
                "pharmacy, and can order lab tests or scans that raise the bill.",
                "Doctor availability is real — timings plus per-day leave — so reception "
                "only offers doctors who are actually sitting, and every visit is on one "
                "patient record with full history."]),
            ("Built for a real clinic", [
                "It works on a phone at the desk, keeps working with no internet, bills "
                "cash, insurance and the Sehat Card, and carries the clinic's own name "
                "and logo. Turn on only the modules you need and add wards, imaging or "
                "multi-branch later as the clinic grows."]),
        ],
        "faqs": [
            ("What is clinic management software?",
             "Clinic management software runs a clinic's front desk and care: patient "
             "registration, OPD appointments, prescriptions, lab and imaging, and "
             "billing — from one connected system."),
            ("Does it manage doctor appointments and schedules?",
             "Yes — doctors have weekly OPD timings and per-day availability, and "
             "reception books against the doctor who is actually sitting, with printed "
             "token slips."),
            ("Is there a free trial?",
             "Yes — a live demo with sample data across every module is available with "
             "no signup."),
        ],
    },
}


def public_pages():
    """The anonymous, crawlable pages, as `(path, label)` for a nav strip.

    Built from `CONTENT_PAGES` rather than written out again, so adding a page
    there gives it a link as well as a URL and a sitemap entry — a page nobody
    can click is a page only a crawler ever reads, which is what these were.
    """
    return ([("/features/", "Features")]
            + [(f"/{slug}/", page["nav"]) for slug, page in CONTENT_PAGES.items()])


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
            "@type": "WebSite",
            "name": BRAND,
            "alternateName": f"{BRAND} — {TAGLINE}",
            "url": base,
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
    from django.templatetags.static import static
    base = _base_url()
    return render(request, "seo/landing.html", {
        "base_url": base,
        "brand": BRAND,
        "tagline": TAGLINE,
        "summary": SUMMARY,
        "features": FEATURES,
        "faqs": FAQS,
        # `/features/` IS this page's home. The root used to render it too and the
        # canonical pointed there, but `/` now redirects anonymous visitors to the
        # sign-in page — a canonical aimed at a redirect gets the page dropped from
        # the index entirely, so it must name the URL that actually serves content.
        "canonical": base + "/features/",
        "og_image": base + static("img/sehatyar-logo.png"),
        "jsonld": _jsonld(base),
    })


def _content_jsonld(base, slug, page):
    url = f"{base}/{slug}/"
    blocks = [
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": page["h1"],
            "url": url,
            "description": page["meta"],
            "isPartOf": {"@type": "WebSite", "name": BRAND, "url": base},
            "about": {"@type": "SoftwareApplication", "name": BRAND, "url": base},
        },
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": base + "/"},
                {"@type": "ListItem", "position": 2, "name": page["h1"], "item": url},
            ],
        },
    ]
    if page.get("faqs"):
        blocks.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in page["faqs"]],
        })
    return mark_safe(json.dumps(blocks, ensure_ascii=False, indent=2))


def content_page(request, slug):
    """One keyword-targeted marketing page (see CONTENT_PAGES). Public, tenant-free."""
    from django.http import Http404
    from django.templatetags.static import static
    page = CONTENT_PAGES.get(slug)
    if page is None:
        raise Http404("No such page")
    base = _base_url()
    return render(request, "seo/content_page.html", {
        "base_url": base, "brand": BRAND, "tagline": TAGLINE,
        "slug": slug, "page": page, "features": FEATURES,
        "canonical": f"{base}/{slug}/",
        "og_image": base + static("img/sehatyar-logo.png"),
        "jsonld": _content_jsonld(base, slug, page),
    })


def home(request):
    """The site root `/`.

    - Signed-in users get the app dashboard exactly as before.
    - **Anyone else gets the sign-in page**, on every host — the bare platform
      domain, a tenant subdomain, and the desktop/LAN build alike.

    The root briefly served the marketing landing to anonymous visitors instead,
    on the SEO reasoning that a homepage a crawler can read outranks a login
    wall. That is true in the abstract and wrong for this product: `sehatyar.online`
    is what staff type to get to work, and landing them on a brochure instead of
    the sign-in form is a worse cost every day than the ranking is worth. The
    marketing page keeps its own URL at `/features/` (linked from the sign-in
    navbar, canonical, in the sitemap at priority 1.0), so nothing is
    unreachable or unindexable — it just is not what the front door opens onto.
    """
    if request.user.is_authenticated:
        from inventory.views import dashboard
        return dashboard(request)
    from django.shortcuts import redirect
    return redirect("login")


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
    # `/features/` first, then the keyword content pages, then demo. The bare root
    # is deliberately NOT listed: it redirects anonymous visitors to the sign-in
    # page, and a sitemap that advertises a redirect wastes crawl budget and
    # teaches the crawler the homepage has nothing on it.
    content = [f"/{slug}/" for slug in CONTENT_PAGES]
    urls = ["/features/"] + content + ["/demo/"]

    def _priority(u):
        if u == "/features/":
            return "1.0"
        if u in content:
            return "0.9"
        return "0.7"

    items = "".join(
        f"<url><loc>{base}{u}</loc><changefreq>weekly</changefreq>"
        f"<priority>{_priority(u)}</priority></url>"
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
