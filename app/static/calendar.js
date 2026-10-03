document.addEventListener('DOMContentLoaded', () => {
  const hiddenRAs = new Set();
  let showCancelled = true;

  const visible = (ev) => {
    if (!showCancelled && ev.extendedProps.status === 'cancelled') return false;
    const ras = ev.extendedProps.ras;
    if (ras.length === 0) return true;
    return ras.some((r) => !hiddenRAs.has(r));
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
        + (p.status === 'cancelled' ? '\nCANCELLED' : '');
    },
    eventClick: (info) => {
      const ev = info.event;
      const p = ev.extendedProps;
      const fmt = { weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' };
      document.getElementById('d-study').textContent = p.study;
      const status = document.getElementById('d-status');
      status.textContent = p.status === 'cancelled' ? 'CANCELLED' : 'Scheduled';
      status.className = p.status;
      document.getElementById('d-when').textContent =
        ev.start.toLocaleString([], fmt) + (ev.end ? ' – ' + ev.end.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '');
      document.getElementById('d-location').textContent = p.location || '—';
      document.getElementById('d-ra').textContent = p.raNames.join(', ') || '— (no RA tag found)';
      document.getElementById('d-participant').textContent = p.participant || '—';
      document.getElementById('details').showModal();
    },
  });
  calendar.render();

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
