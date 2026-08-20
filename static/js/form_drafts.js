/**
 * Form Drafts Auto-Save Utility
 * Automatically preserves unsaved form inputs in localStorage keyed by form ID/URL.
 * Restores drafts with a user prompt on reload, and clears on successful submit.
 */
(function () {
  function initFormDraft(formEl, draftKey) {
    if (!formEl || !draftKey) return;

    const storageKey = `sehatyar_draft_${draftKey}`;

    // Check if there is an existing draft on load
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      try {
        const data = JSON.parse(saved);
        if (data && typeof data === 'object' && Object.keys(data).length > 0) {
          showRestoreBanner(formEl, data, storageKey);
        }
      } catch (e) {
        localStorage.removeItem(storageKey);
      }
    }

    // Auto-save on input change (debounced)
    let timer = null;
    formEl.addEventListener('input', function (e) {
      if (e.target.type === 'password' || e.target.type === 'hidden') return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        saveDraft(formEl, storageKey);
      }, 500);
    });

    // Clear draft on submit
    formEl.addEventListener('submit', function () {
      localStorage.removeItem(storageKey);
    });
  }

  function saveDraft(formEl, storageKey) {
    const formData = {};
    const elements = formEl.elements;
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      if (!el.name || el.type === 'password' || el.type === 'hidden' || el.type === 'submit') continue;

      if (el.type === 'checkbox') {
        formData[el.name] = el.checked;
      } else if (el.type === 'radio') {
        if (el.checked) formData[el.name] = el.value;
      } else {
        if (el.value.trim() !== '') {
          formData[el.name] = el.value;
        }
      }
    }

    if (Object.keys(formData).length > 0) {
      localStorage.setItem(storageKey, JSON.stringify(formData));
    } else {
      localStorage.removeItem(storageKey);
    }
  }

  function showRestoreBanner(formEl, data, storageKey) {
    const banner = document.createElement('div');
    banner.className = 'draft-restore-banner';
    banner.innerHTML = `
      <div class="draft-restore-content">
        <span>📝 <strong>Unsaved draft found</strong> from your previous session. Would you like to restore it?</span>
        <div class="draft-restore-actions">
          <button type="button" class="btn sm" id="btnRestoreDraft">Restore Draft</button>
          <button type="button" class="btn sm alt" id="btnDiscardDraft">Discard</button>
        </div>
      </div>
    `;

    formEl.parentNode.insertBefore(banner, formEl);

    banner.querySelector('#btnRestoreDraft').addEventListener('click', function () {
      applyDraft(formEl, data);
      banner.remove();
      if (window.showToast) window.showToast("Draft restored successfully!");
    });

    banner.querySelector('#btnDiscardDraft').addEventListener('click', function () {
      localStorage.removeItem(storageKey);
      banner.remove();
    });
  }

  function applyDraft(formEl, data) {
    const elements = formEl.elements;
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      if (el.name && data[el.name] !== undefined) {
        if (el.type === 'checkbox') {
          el.checked = Boolean(data[el.name]);
        } else if (el.type === 'radio') {
          el.checked = (el.value === data[el.name]);
        } else {
          el.value = data[el.name];
        }
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
  }

  // Auto-bind any form with data-auto-draft attribute
  document.addEventListener('DOMContentLoaded', function () {
    const draftForms = document.querySelectorAll('form[data-auto-draft]');
    draftForms.forEach(form => {
      const key = form.getAttribute('data-auto-draft') || window.location.pathname;
      initFormDraft(form, key);
    });
  });

  window.initFormDraft = initFormDraft;
})();
