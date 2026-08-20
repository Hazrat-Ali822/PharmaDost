/**
 * Global Command Palette (Ctrl + K / Cmd + K)
 * Fast keyboard-first search over navigation, patients, and medicines.
 */
(function () {
  let paletteModal = null;
  let paletteInput = null;
  let paletteResults = null;
  let selectedIndex = 0;
  let currentItems = [];
  let patientIndexData = null;
  let isFetchingPatients = false;

  // Static navigation routes derived from app capabilities
  const navRoutes = [
    { title: "Create Bill / POS", category: "Pharmacy", url: "/sales/new/", icon: "🛒", kbd: "F2" },
    { title: "Bills List", category: "Pharmacy", url: "/sales/list/", icon: "📄" },
    { title: "Wholesale Orders", category: "Pharmacy", url: "/sales/wholesale/", icon: "📦" },
    { title: "Medicines & Stock Inventory", category: "Pharmacy", url: "/medicines/", icon: "💊", kbd: "F5" },
    { title: "Purchase Orders", category: "Pharmacy", url: "/medicines/purchase-orders/", icon: "📋" },
    { title: "Suppliers", category: "Pharmacy", url: "/suppliers/", icon: "🏭" },
    { title: "Customers (Khata)", category: "Pharmacy", url: "/customers/", icon: "👥" },
    { title: "Patients Registry", category: "Clinical", url: "/patients/", icon: "🧑‍🤝‍🧑", kbd: "F3" },
    { title: "Add New Patient", category: "Clinical", url: "/patients/add/", icon: "➕" },
    { title: "OPD Appointments / Front Desk", category: "Clinical", url: "/opd/reception/", icon: "🩺", kbd: "F4" },
    { title: "Appointments List", category: "Clinical", url: "/opd/", icon: "📅" },
    { title: "Prescriptions", category: "Clinical", url: "/prescriptions/", icon: "📝" },
    { title: "Inpatients (IPD / Ward)", category: "Clinical", url: "/ipd/", icon: "🛏️" },
    { title: "Nursing Board", category: "Clinical", url: "/ipd/board/", icon: "📊" },
    { title: "Shift Handover Board", category: "Clinical", url: "/ipd/handover/", icon: "🔄" },
    { title: "Emergency / Casualty", category: "Clinical", url: "/emergency/", icon: "🚨" },
    { title: "Ambulance Dispatch", category: "Clinical", url: "/ambulance/", icon: "🚑" },
    { title: "Operation Theatre (OT)", category: "Clinical", url: "/ot/", icon: "✂️" },
    { title: "Maternity / ANC", category: "Clinical", url: "/maternity/", icon: "👶" },
    { title: "Vaccination / EPI", category: "Clinical", url: "/vaccination/", icon: "💉" },
    { title: "Blood Bank", category: "Clinical", url: "/bloodbank/", icon: "🩸" },
    { title: "Lab Orders", category: "Diagnostics", url: "/lab/orders/", icon: "🧪" },
    { title: "Imaging / Scans", category: "Diagnostics", url: "/imaging/studies/", icon: "🩻" },
    { title: "Patient Billing / Invoices", category: "Finance", url: "/billing/", icon: "💳", kbd: "F6" },
    { title: "Panels / Sehat Card Claims", category: "Finance", url: "/panels/", icon: "🛡️" },
    { title: "Expenses", category: "Finance", url: "/expenses/", icon: "💸", kbd: "F8" },
    { title: "Cash Closing", category: "Finance", url: "/cash-closing/", icon: "🔒", kbd: "F9" },
    { title: "Doctor Payouts", category: "Finance", url: "/opd/payouts/", icon: "💰" },
    { title: "Sales & Inventory Reports", category: "Reports", url: "/reports/sales/", icon: "📈" },
    { title: "Profit Report", category: "Reports", url: "/reports/profit/", icon: "📊" },
    { title: "Day Book", category: "Reports", url: "/reports/day-book/", icon: "📖" },
    { title: "Staff & Attendance (HR)", category: "Staff", url: "/hr/", icon: "👤" },
    { title: "Users & Access Control", category: "System", url: "/manage/users/", icon: "🔐" },
    { title: "Settings & Branding", category: "System", url: "/manage/settings/", icon: "⚙️" },
    { title: "Audit Log", category: "System", url: "/manage/audit/", icon: "🛡️" },
    { title: "Offline Queue", category: "System", url: "/offline/queue/", icon: "📶" }
  ];

  function createPaletteUI() {
    if (document.getElementById('cmdPaletteModal')) return;

    const modal = document.createElement('div');
    modal.id = 'cmdPaletteModal';
    modal.className = 'cmd-palette-backdrop';
    modal.innerHTML = `
      <div class="cmd-palette-card" role="dialog" aria-modal="true">
        <div class="cmd-palette-input-wrap">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="cmd-palette-search-icon">
            <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input type="text" id="cmdPaletteInput" class="cmd-palette-input" placeholder="Type to search patients, medicines, or pages... (Esc to close)" autocomplete="off" />
          <span class="cmd-palette-kbd">ESC</span>
        </div>
        <div class="cmd-palette-results" id="cmdPaletteResults"></div>
        <div class="cmd-palette-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> Navigate</span>
          <span><kbd>↵</kbd> Select</span>
          <span><kbd>ESC</kbd> Close</span>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    paletteModal = modal;
    paletteInput = document.getElementById('cmdPaletteInput');
    paletteResults = document.getElementById('cmdPaletteResults');

    modal.addEventListener('click', function (e) {
      if (e.target === modal) closePalette();
    });

    paletteInput.addEventListener('input', handleSearch);
    paletteInput.addEventListener('keydown', handleKeyDown);
  }

  function openPalette() {
    createPaletteUI();
    paletteModal.classList.add('open');
    paletteInput.value = '';
    selectedIndex = 0;
    fetchOfflinePatientIndex();
    renderDefaultResults();
    setTimeout(() => paletteInput.focus(), 50);
  }

  function closePalette() {
    if (paletteModal) {
      paletteModal.classList.remove('open');
    }
  }

  function fetchOfflinePatientIndex() {
    if (patientIndexData && patientIndexData.length > 0) return;

    // Check cached copy in sessionStorage for instant search
    try {
      const cached = sessionStorage.getItem('sehatyar_patient_index');
      if (cached) {
        patientIndexData = JSON.parse(cached);
      }
    } catch (e) {}

    if (isFetchingPatients) return;
    isFetchingPatients = true;

    fetch('/patients/index.json')
      .then(res => {
        if (!res.ok) throw new Error('Patient index unreachable');
        return res.json();
      })
      .then(data => {
        const list = data && Array.isArray(data.patients) ? data.patients : (Array.isArray(data) ? data : []);
        if (list.length > 0) {
          patientIndexData = list;
          try {
            sessionStorage.setItem('sehatyar_patient_index', JSON.stringify(list));
          } catch (e) {}
        }
      })
      .catch(() => {
        if (!patientIndexData) patientIndexData = [];
      })
      .finally(() => {
        isFetchingPatients = false;
      });
  }

  function renderDefaultResults() {
    currentItems = navRoutes.slice(0, 8);
    renderList(currentItems, "Frequently Used Pages");
  }

  function handleSearch() {
    const rawQ = paletteInput.value.trim();
    const q = rawQ.toLowerCase();
    selectedIndex = 0;

    if (!q) {
      renderDefaultResults();
      return;
    }

    const qWords = q.split(/\s+/).filter(Boolean);
    const qDigits = q.replace(/\D/g, '');

    const matchedNav = navRoutes.filter(item =>
      item.title.toLowerCase().includes(q) ||
      item.category.toLowerCase().includes(q)
    ).slice(0, 4);

    let matchedPatients = [];
    if (patientIndexData && patientIndexData.length > 0) {
      matchedPatients = patientIndexData.filter(p => {
        const name = (p.name || p.full_name || '').toLowerCase();
        const mrn = (p.mrn || '').toLowerCase();
        const phone = (p.phone || '').replace(/[\s-]/g, '').toLowerCase();
        const cnic = (p.cnic || '').replace(/[\s-]/g, '').toLowerCase();

        // Multi-word patient name matching: all search words must match in the patient name
        const nameMatch = qWords.length > 0 && qWords.every(w => name.includes(w));
        const mrnMatch = mrn.includes(q);
        const phoneMatch = qDigits.length >= 3 && phone.includes(qDigits);
        const cnicMatch = qDigits.length >= 3 && cnic.includes(qDigits);

        return nameMatch || mrnMatch || phoneMatch || cnicMatch;
      }).slice(0, 8).map(p => {
        const patientId = p.pk || p.id;
        const patientName = p.name || p.full_name || 'Patient';
        return {
          title: `${patientName} (MRN: ${p.mrn || '—'})`,
          category: "Patient",
          subtitle: `Phone: ${p.phone || '—'} · Gender/Age: ${p.gender || '—'} ${p.age || ''}`,
          url: `/patients/${patientId}/`,
          icon: "🧑"
        };
      });
    }

    // Include full registry search option
    const fullRegistrySearch = {
      title: `Search Registry for "${rawQ}"`,
      category: "Search",
      subtitle: `Open full patient list filtered by "${rawQ}"`,
      url: `/patients/?q=${encodeURIComponent(rawQ)}`,
      icon: "🔍"
    };

    currentItems = [...matchedNav, ...matchedPatients, fullRegistrySearch];
    renderList(currentItems, `Search Results for "${rawQ}"`);
  }

  function renderList(items, sectionTitle) {
    if (!paletteResults) return;

    if (items.length === 0) {
      paletteResults.innerHTML = `
        <div class="cmd-palette-empty">
          <p>No matching pages or patients found.</p>
          <small>Press Enter to search the entire patient registry.</small>
        </div>
      `;
      return;
    }

    let html = `<div class="cmd-palette-section-title">${sectionTitle}</div>`;
    items.forEach((item, idx) => {
      const isSelected = idx === selectedIndex;
      html += `
        <div class="cmd-palette-item ${isSelected ? 'selected' : ''}" data-idx="${idx}" onclick="window.location.href='${item.url}'">
          <div class="cmd-palette-item-icon">${item.icon || '📌'}</div>
          <div class="cmd-palette-item-content">
            <div class="cmd-palette-item-title">${escapeHtml(item.title)}</div>
            ${item.subtitle ? `<div class="cmd-palette-item-subtitle">${escapeHtml(item.subtitle)}</div>` : ''}
          </div>
          <div class="cmd-palette-item-meta">
            ${item.kbd ? `<kbd>${item.kbd}</kbd>` : `<span class="cmd-badge">${item.category}</span>`}
          </div>
        </div>
      `;
    });

    paletteResults.innerHTML = html;

    const selectedEl = paletteResults.querySelector('.cmd-palette-item.selected');
    if (selectedEl) {
      selectedEl.scrollIntoView({ block: 'nearest' });
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Escape') {
      closePalette();
      e.preventDefault();
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (currentItems.length > 0) {
        selectedIndex = (selectedIndex + 1) % currentItems.length;
        updateSelection();
      }
      return;
    }

    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (currentItems.length > 0) {
        selectedIndex = (selectedIndex - 1 + currentItems.length) % currentItems.length;
        updateSelection();
      }
      return;
    }

    if (e.key === 'Enter') {
      e.preventDefault();
      if (currentItems[selectedIndex] && currentItems[selectedIndex].url) {
        window.location.href = currentItems[selectedIndex].url;
      } else {
        const query = paletteInput ? paletteInput.value.trim() : '';
        if (query) {
          window.location.href = `/patients/?q=${encodeURIComponent(query)}`;
        }
      }
    }
  }

  function updateSelection() {
    const items = paletteResults.querySelectorAll('.cmd-palette-item');
    items.forEach((el, idx) => {
      if (idx === selectedIndex) {
        el.classList.add('selected');
        el.scrollIntoView({ block: 'nearest' });
      } else {
        el.classList.remove('selected');
      }
    });
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Pre-fetch on idle so patient search is instantaneous
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(fetchOfflinePatientIndex, 500);
  } else {
    document.addEventListener('DOMContentLoaded', () => setTimeout(fetchOfflinePatientIndex, 500));
  }

  // Global Key Listener: Ctrl + K or Cmd + K
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (paletteModal && paletteModal.classList.contains('open')) {
        closePalette();
      } else {
        openPalette();
      }
    }
  });

  window.openCommandPalette = openPalette;
  window.closeCommandPalette = closePalette;
})();
