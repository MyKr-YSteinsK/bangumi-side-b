(() => {
  const root = document.querySelector('[data-quarter]');
  if (!root) return;
  const quarter = root.dataset.quarter;
  const mount = document.querySelector('#detail-mount');
  const dataUrl = `../data/quarters/${quarter}.json`;
  let payload = null;
  fetch(dataUrl)
    .then((response) => response.json())
    .then((value) => { payload = value; });
  document.querySelectorAll('[data-subject-id]').forEach((card) => {
    card.addEventListener('click', () => {
      if (!payload || !mount) return;
      const id = Number(card.dataset.subjectId);
      const groups = [
        payload.tv.premiere,
        payload.tv.continuing,
        payload.movie.premiere,
      ];
      const item = groups.flat().find((entry) => entry.subject_id === id);
      if (!item) return;
      mount.hidden = false;
      mount.textContent = item.display_summary || item.preferred_title;
    });
  });
})();
