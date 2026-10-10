(function () {
  'use strict';

  var COUNTRIES = ('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL ' +
    'BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV ' +
    'CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD ' +
    'GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM ' +
    'IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK ' +
    'LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW ' +
    'MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR ' +
    'PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ' +
    'ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY ' +
    'UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW').split(' ');

  var regionName = (function () {
    try {
      var names = new Intl.DisplayNames(['en'], { type: 'region' });
      return function (code) { return names.of(code) || code; };
    } catch (e) {
      return function (code) { return code; };
    }
  })();

  function $(id) { return document.getElementById(id); }

  // Days since Red Cross visits stopped (7 October 2023).
  function updateDays() {
    var start = Date.UTC(2023, 9, 7);
    var now = new Date();
    var today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    var days = Math.floor((today - start) / 86400000);
    if (days > 0) $('days').textContent = days.toLocaleString('en');
  }

  function setupCopy() {
    document.querySelectorAll('[data-copy]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var el = $(btn.getAttribute('data-copy'));
        var msg = btn.parentNode.querySelector('.copied');
        var text = el.innerText.trim();
        function selectFallback() {
          var d = el.closest('details'); if (d) d.open = true;
          var r = document.createRange(); r.selectNodeContents(el);
          var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          msg.textContent = 'Text selected. Press copy on your device.';
        }
        try {
          navigator.clipboard.writeText(text).then(function () { msg.textContent = 'Copied'; }, selectFallback);
        } catch (e) { selectFallback(); }
      });
    });
  }

  function setupCountries() {
    var select = $('f-country');
    var guess = ((navigator.language || '').split('-')[1] || '').toUpperCase();
    COUNTRIES
      .map(function (code) { return { code: code, name: regionName(code) }; })
      .sort(function (a, b) { return a.name.localeCompare(b.name, 'en'); })
      .forEach(function (c) {
        var opt = document.createElement('option');
        opt.value = c.code;
        opt.textContent = c.name;
        if (c.code === guess) opt.selected = true;
        select.appendChild(opt);
      });
  }

  function renderCounts(data) {
    $('sig-total').textContent = (data.total || 0).toLocaleString('en');
    var list = $('sig-countries');
    list.textContent = '';
    var entries = Object.keys(data.countries || {}).map(function (code) {
      return [code, data.countries[code]];
    }).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 10);
    entries.forEach(function (e) {
      var li = document.createElement('li');
      var name = document.createElement('span');
      name.textContent = regionName(e[0]);
      var n = document.createElement('span');
      n.textContent = e[1].toLocaleString('en');
      li.appendChild(name);
      li.appendChild(n);
      list.appendChild(li);
    });
  }

  function loadCounts() {
    return fetch('/api/count', { headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(renderCounts)
      .catch(function () { /* keep the last numbers shown */ });
  }

  function setupForm() {
    var form = $('sign-form');
    var email = $('f-email');
    var updates = $('f-updates');
    var msg = $('f-msg');
    var submit = $('f-submit');

    function say(text, kind) {
      msg.textContent = text;
      msg.className = 'formmsg' + (kind ? ' ' + kind : '');
    }

    email.addEventListener('input', function () {
      var has = email.value.trim() !== '';
      updates.disabled = !has;
      if (!has) updates.checked = false;
    });

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var name = $('f-name').value.trim();
      var town = $('f-town').value.trim();
      var country = $('f-country').value;
      if (!name) { say('Enter your full name.', 'err'); $('f-name').focus(); return; }
      if (!town) { say('Enter your town or city.', 'err'); $('f-town').focus(); return; }
      if (!country) { say('Choose your country from the list.', 'err'); $('f-country').focus(); return; }
      if (email.value.trim() && !email.checkValidity()) { say('Check your email address, or leave it blank.', 'err'); email.focus(); return; }
      if (!$('f-consent').checked) { say('Tick the box to agree to your name being added to the petition.', 'err'); $('f-consent').focus(); return; }

      submit.disabled = true;
      say('Adding your name…');
      fetch('/api/sign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          name: name,
          town: town,
          country: country,
          email: email.value.trim(),
          updates: updates.checked,
          consent: true,
          website: $('f-website').value
        })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) { return { ok: r.ok, body: body }; });
      }).then(function (res) {
        if (res.ok) {
          form.reset();
          updates.disabled = true;
          say('Thank you. Your name is on the petition. Now share this page with three people.', 'ok');
          loadCounts();
        } else {
          say(res.body.error || 'Your signature was not saved. Try again in a minute.', 'err');
        }
      }).catch(function () {
        say('Your signature was not saved because the connection failed. Try again in a minute.', 'err');
      }).then(function () {
        submit.disabled = false;
      });
    });
  }

  updateDays();
  setupCopy();
  setupCountries();
  setupForm();
  loadCounts();
  setInterval(function () { if (!document.hidden) loadCounts(); }, 60000);
})();
