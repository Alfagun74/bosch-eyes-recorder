/* Bosch Eyes Recorder — UI behaviour (no external dependencies) */
(function () {
  'use strict';

  var root = document.documentElement;

  function initTheme() {
    var button = document.getElementById('theme-toggle');
    if (!button) return;
    function setLabel() {
      var dark = root.getAttribute('data-theme') !== 'light';
      button.setAttribute('aria-label', dark ? 'Helles Design aktivieren' : 'Dunkles Design aktivieren');
      button.title = button.getAttribute('aria-label');
    }
    setLabel();
    button.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) { /* ignore */ }
      setLabel();
    });
  }

  function initNav() {
    var page = document.body.getAttribute('data-page');
    document.querySelectorAll('[data-nav]').forEach(function (link) {
      if (link.getAttribute('data-nav') === page) link.classList.add('is-active');
    });
  }

  function initLive() {
    var pending = document.getElementById('live-pending');
    if (pending && pending.getAttribute('data-refresh') === '1') {
      var timer = setInterval(function () {
        fetch('/status', { cache: 'no-store', credentials: 'same-origin' })
          .then(function (response) { return response.ok ? response.json() : null; })
          .then(function (status) {
            if (status && status.media && status.media.path_ready) {
              clearInterval(timer);
              window.location.reload();
            }
          })
          .catch(function () { /* keep retrying */ });
      }, 5000);
    }
    var video = document.getElementById('live');
    if (!video) return;
    var hlsUrl = video.getAttribute('data-hls-url');
    if (!hlsUrl) return;
    function start() {
      if (window.Hls && window.Hls.isSupported()) {
        var hls = new window.Hls({ lowLatencyMode: true, liveSyncDurationCount: 3, enableWorker: true });
        hls.loadSource(hlsUrl);
        hls.attachMedia(video);
        window.__liveHls = hls;
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = hlsUrl;
      } else {
        video.src = hlsUrl;
      }
    }
    if (window.Hls) {
      start();
    } else {
      var script = document.createElement('script');
      script.src = '/static/hls.min.js';
      script.onload = start;
      script.onerror = function () { video.src = hlsUrl; };
      document.head.appendChild(script);
    }

  }

  function initRecordings() {
    var dataElement = document.getElementById('recordings-data');
    var player = document.getElementById('recording');
    if (!dataElement || !player) return;
    var payload;
    try { payload = JSON.parse(dataElement.textContent || '{}'); } catch (e) { return; }
    var all = (payload.items || []).slice().sort(function (a, b) {
      if (a.date !== b.date) return a.date < b.date ? -1 : 1;
      return a.time < b.time ? -1 : (a.time > b.time ? 1 : 0);
    });
    if (!all.length) return;

    var dateInput = document.getElementById('rec-date');
    var fromInput = document.getElementById('rec-from');
    var toInput = document.getElementById('rec-to');
    var chips = document.getElementById('day-chips');
    var timeline = document.getElementById('timeline');
    var timelineTrack = document.getElementById('timeline-track');
    var timelineLegend = document.getElementById('timeline-legend');
    var timelineNote = document.getElementById('timeline-note');
    var list = document.getElementById('segment-list');
    var empty = document.getElementById('segments-empty');
    var nowPlaying = document.getElementById('now-playing');
    var summary = document.getElementById('filter-summary');
    var reset = document.getElementById('filter-reset');

    var state = { items: [], currentIndex: -1, chain: false, date: '', from: '', to: '' };

    function esc(value) {
      return String(value).replace(/[&<>"']/g, function (ch) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
      });
    }
    function fmtDay(date) {
      var parsed = new Date(date + 'T00:00:00');
      if (isNaN(parsed.getTime())) return date;
      return parsed.toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' });
    }
    function fmtHour(hour) { return hour + ' Uhr'; }
    function secondsOf(time) {
      var parts = String(time || '00:00:00').split(':').map(Number);
      return (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
    }
    function matches(item) {
      if (state.date && item.date !== state.date) return false;
      if (state.from && item.time < state.from) return false;
      if (state.to && item.time > state.to) return false;
      return true;
    }
    function visible() { return all.filter(matches); }

    function renderDayChips() {
      var counts = {};
      all.forEach(function (item) { counts[item.date] = (counts[item.date] || 0) + 1; });
      var dates = Object.keys(counts).sort().reverse();
      chips.innerHTML = '';
      var allChip = document.createElement('button');
      allChip.type = 'button';
      allChip.className = 'chip';
      allChip.textContent = 'Alle Tage';
      allChip.setAttribute('aria-pressed', String(!state.date));
      allChip.addEventListener('click', function () { state.date = ''; dateInput.value = ''; apply(); });
      chips.appendChild(allChip);
      dates.forEach(function (date) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.setAttribute('aria-pressed', String(state.date === date));
        chip.innerHTML = esc(fmtDay(date).replace(/^[A-Za-zÄÖÜäöü]+\s/, '')) +
          ' <small>' + counts[date] + '</small>';
        chip.title = fmtDay(date) + ' (' + counts[date] + ' Segmente)';
        chip.addEventListener('click', function () {
          state.date = date; dateInput.value = date; apply();
        });
        chips.appendChild(chip);
      });
    }

    function renderTimeline(items) {
      if (!timeline) return;
      var show = !!state.date;
      timeline.hidden = !show;
      if (!show) return;
      timelineTrack.innerHTML = '';
      for (var hour = 0; hour <= 24; hour += 3) {
        var marker = document.createElement('span');
        marker.className = 'timeline-hour';
        marker.style.left = (hour / 24 * 100) + '%';
        var label = document.createElement('span');
        label.textContent = hour + ':00';
        marker.appendChild(label);
        timelineTrack.appendChild(marker);
      }
      items.forEach(function (item) {
        var index = state.items.indexOf(item);
        var block = document.createElement('button');
        block.type = 'button';
        block.className = 'timeline-block' + (item.codec === 'AV1' ? ' is-av1' : '');
        block.style.left = (secondsOf(item.time) / 86400 * 100) + '%';
        block.style.width = Math.max(0.45, 600 / 86400 * 100) + '%';
        block.title = item.label + ' · ' + item.size_human + ' · ' + item.codec;
        block.setAttribute('aria-label', 'Segment ' + item.label + ' abspielen');
        block.addEventListener('click', function () { playAt(index, false); });
        timelineTrack.appendChild(block);
      });
      if (timelineLegend) {
        timelineLegend.innerHTML = '<span><span class="legend-dot"></span>H.264</span>' +
          '<span><span class="legend-dot av1"></span>AV1</span>';
      }
      if (timelineNote) timelineNote.textContent = fmtDay(state.date) + ' · ' + items.length + ' Segmente';
    }

    function renderList(items) {
      list.innerHTML = '';
      if (empty) empty.hidden = items.length !== 0;
      var active = state.items[state.currentIndex] && state.items[state.currentIndex].name;
      var groups = {};
      var order = [];
      items.forEach(function (item) {
        var key = item.date + '|' + item.time.slice(0, 2);
        if (!groups[key]) { groups[key] = []; order.push(key); }
        groups[key].push(item);
      });
      order.forEach(function (key) {
        var parts = key.split('|');
        var heading = document.createElement('div');
        heading.className = 'day-heading';
        heading.innerHTML = '<h2>' + esc(fmtDay(parts[0])) + ' · ' + esc(fmtHour(parseInt(parts[1], 10))) + '</h2>';
        var listWrap = document.createElement('div');
        listWrap.className = 'segment-list';
        groups[key].forEach(function (item) {
          var index = state.items.indexOf(item);
          var row = document.createElement('div');
          row.className = 'segment' + (item.name === active ? ' is-active' : '');
          row.setAttribute('data-index', String(index));
          row.innerHTML =
            '<div class="seg-time">' + esc(item.time.slice(0, 5)) + '</div>' +
            '<div class="seg-meta"><span class="badge-codec ' + (item.codec === 'AV1' ? 'av1' : '') + '">' +
            esc(item.codec) + '</span><span>' + esc(item.size_human) + '</span>' +
            '<span class="muted">' + esc(item.ext.toUpperCase()) + '</span></div>' +
            '<div class="seg-actions">' +
            '<button class="btn btn-sm" type="button" data-action="play">&#9654; Abspielen</button>' +
            '<button class="btn btn-sm" type="button" data-action="chain">Ab hier</button>' +
            '<a class="btn btn-sm btn-ghost" href="' + esc(item.url) + '" download>Download</a>' +
            '</div>';
          row.querySelector('[data-action="play"]').addEventListener('click', function () { playAt(index, false); });
          row.querySelector('[data-action="chain"]').addEventListener('click', function () { playAt(index, true); });
          listWrap.appendChild(row);
        });
        var group = document.createElement('section');
        group.className = 'day-group';
        group.appendChild(heading);
        group.appendChild(listWrap);
        list.appendChild(group);
      });
    }

    function markActive() {
      var current = state.items[state.currentIndex];
      document.querySelectorAll('#segment-list .segment').forEach(function (row) {
        var index = parseInt(row.getAttribute('data-index'), 10);
        row.classList.toggle('is-active', !!current && index === state.currentIndex);
      });
      document.querySelectorAll('.timeline-block').forEach(function (block, index) {
        block.classList.toggle('is-active', index === state.currentIndex);
      });
    }

    function playAt(index, chain) {
      if (index < 0 || index >= state.items.length) return;
      state.currentIndex = index;
      state.chain = !!chain;
      var item = state.items[index];
      player.src = item.url;
      var promise = player.play();
      if (promise && promise.catch) promise.catch(function () { /* controls are visible */ });
      if (nowPlaying) nowPlaying.innerHTML = '<span class="pill ok"><span class="dot"></span>Wiedergabe</span> ' +
        '<strong>' + esc(item.label) + '</strong> <span class="muted">' + esc(item.codec) + ' · ' + esc(item.size_human) + '</span>';
      markActive();
      player.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    player.addEventListener('play', function () {
      var hint = document.getElementById('player-hint');
      if (hint) hint.hidden = true;
    });

    player.addEventListener('ended', function () {
      if (state.chain && state.currentIndex + 1 < state.items.length) {
        playAt(state.currentIndex + 1, true);
      } else if (nowPlaying) {
        state.chain = false;
        nowPlaying.innerHTML = '<span class="pill">Ende</span> <span class="muted">Kein weiteres Segment in dieser Auswahl.</span>';
      }
    });

    function syncUrl() {
      try {
        var params = new URLSearchParams(window.location.search);
        if (state.date) params.set('date', state.date); else params.delete('date');
        if (state.from) params.set('from', state.from); else params.delete('from');
        if (state.to) params.set('to', state.to); else params.delete('to');
        var query = params.toString();
        window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
      } catch (e) { /* ignore */ }
    }

    function apply() {
      state.items = visible();
      renderDayChips();
      renderTimeline(state.items);
      renderList(state.items);
      if (summary) {
        var total = state.items.reduce(function (sum, item) { return sum + item.size; }, 0);
        summary.textContent = state.items.length + ' Segmente · ' + humanBytes(total) +
          (state.date ? ' · ' + fmtDay(state.date) : '');
      }
      syncUrl();
    }

    function humanBytes(bytes) {
      if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
      if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
      if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return bytes + ' B';
    }

    function resetFilters() {
      state.from = ''; state.to = '';
      fromInput.value = ''; toInput.value = '';
      dateInput.value = state.date;
      apply();
    }

    var params = new URLSearchParams(window.location.search);
    var dates = all.map(function (item) { return item.date; }).sort();
    var latest = dates[dates.length - 1];
    var requested = params.get('date');
    state.date = requested && dates.indexOf(requested) !== -1 ? requested : latest;
    state.from = params.get('from') || '';
    state.to = params.get('to') || '';
    dateInput.min = dates[0];
    dateInput.max = dates[dates.length - 1];
    dateInput.value = state.date;
    fromInput.value = state.from;
    toInput.value = state.to;
    dateInput.addEventListener('change', function () {
      state.date = dateInput.value && dates.indexOf(dateInput.value) !== -1 ? dateInput.value : '';
      apply();
    });
    fromInput.addEventListener('change', function () { state.from = fromInput.value; apply(); });
    toInput.addEventListener('change', function () { state.to = toInput.value; apply(); });
    if (reset) reset.addEventListener('click', resetFilters);
    apply();
  }

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initNav();
    initLive();
    initRecordings();
  });
})();
