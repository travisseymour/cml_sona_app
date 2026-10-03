document.addEventListener('DOMContentLoaded', () => {
  const hiddenRAs = new Set();
  let showCancelled = true;

  const visible = (ev) => {
    if (!showCancelled && ev.extendedProps.status === 'cancelled') return false;
    const ras = ev.extendedProps.ras;
    if (ras.length === 0) return true;
    return ras.some((r) => !hiddenRAs.has(r));
  };

  let current = null;  // the event shown in the details dialog
  const noShowBox = document.getElementById('d-noshow');

  const showStatus = (p) => {
    const status = document.getElementById('d-status');
    if (p.status === 'cancelled') {
      status.textContent = 'CANCELLED';
    } else if (p.noShow) {
      status.textContent = `NO-SHOW (${p.noShow.toUpperCase()})`;
    } else {
      status.textContent = 'Scheduled';
    }
    status.className = p.noShow ? 'no-show' : p.status;
    noShowBox.hidden = p.status === 'cancelled' || !!p.noShow || new Date() < new Date(p.noShowAfter);
  };

  const calendar = new FullCalendar.Calendar(document.getElementById('calendar'), {
    initialView: window.innerWidth < 700 ? 'listWeek' : 'timeGridWeek',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek',
    },
    buttonText: { listWeek: 'list' },
    nowIndicator: true,
    slotMinTime: '07:00:00',
    slotMaxTime: '21:00:00',
    allDaySlot: false,
    eventDisplay: 'block',
    dayMaxEvents: 6,
    height: 'auto',
    events: (info, success, failure) => {
      const qs = new URLSearchParams({ start: info.startStr, end: info.endStr });
      fetch('/api/events?' + qs)
        .then((r) => (r.status === 401 ? (location.href = '/login', []) : r.json()))
        .then((evs) => success(evs.filter(visible)))
        .catch(failure);
    },
    eventDidMount: (info) => {
      const p = info.event.extendedProps;
      info.el.title = `${p.study}\n${p.location}\nRA: ${p.raNames.join(', ')}`
        + (p.status === 'cancelled' ? '\nCANCELLED' : '')
        + (p.noShow ? `\nNO-SHOW (${p.noShow})` : '');
    },
    eventClick: (info) => {
      const ev = info.event;
      const p = ev.extendedProps;
      const fmt = { weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' };
      document.getElementById('d-study').textContent = p.study;
      current = ev;
      showStatus(p);
      document.getElementById('d-when').textContent =
        ev.start.toLocaleString([], fmt) + (ev.end ? ' – ' + ev.end.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '');
      document.getElementById('d-location').textContent = p.location || '—';
      document.getElementById('d-ra').textContent = p.raNames.join(', ') || '— (no RA tag found)';
      document.getElementById('d-participant').textContent = p.participant || '—';
      document.getElementById('details').showModal();
    },
  });
  calendar.render();

  noShowBox.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!current) return;
      const kind = btn.dataset.noshow;
      const p = current.extendedProps;
      const ok = confirm(
        `Mark this session as an ${kind.toUpperCase()} no-show?\n\n`
        + `${p.study}\n${document.getElementById('d-when').textContent}\n`
        + `Participant: ${p.participant || '—'}\n\n`
        + 'An email will be sent to the lab, and this cannot be undone here.');
      if (!ok) return;
      noShowBox.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      try {
        const r = await fetch(`/api/events/${current.id}/no-show`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type: kind }),
        });
        if (r.status === 401) { location.href = '/login'; return; }
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          alert(data.error || `Something went wrong (HTTP ${r.status}).`);
          calendar.refetchEvents();
          return;
        }
        current.setExtendedProp('noShow', data.noShow);
        showStatus(current.extendedProps);
        calendar.refetchEvents();
      } finally {
        noShowBox.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      }
    });
  });

  const refilter = () => calendar.refetchEvents();

  document.querySelectorAll('#legend input[value]').forEach((box) => {
    box.addEventListener('change', () => {
      box.checked ? hiddenRAs.delete(box.value) : hiddenRAs.add(box.value);
      refilter();
    });
  });
  document.getElementById('show-cancelled').addEventListener('change', (e) => {
    showCancelled = e.target.checked;
    refilter();
  });

  // Pick up new sign-ups without a manual reload.
  setInterval(() => calendar.refetchEvents(), 5 * 60 * 1000);
});
