/* Minimal key binder for the Nebula CDN Astray package: replaces the bundled KeyboardJS
   (whose licence text could not be recovered). Implements only bind.key and bind.axis.
   Axis policy: each axis tracks the directions currently held; when both opposite keys are
   held the most recently pressed one wins, and releasing it falls back to the other one if
   it is still held. Window blur releases everything. Original code for this package, Unlicense. */
(function (w) {
  var names = { left: 37, up: 38, right: 39, down: 40 };
  function code(n) { return names[n] !== undefined ? names[n] : String(n).toUpperCase().charCodeAt(0); }
  var downs = {}, ups = {}, held = {};
  function add(map, c, f) { (map[c] = map[c] || []).push(f); }
  function run(map, c, e) { (map[c] || []).forEach(function (f) { f(e); }); }
  w.addEventListener('keydown', function (e) {
    var c = e.keyCode; if (held[c]) { return; } held[c] = true; run(downs, c, e);
  });
  w.addEventListener('keyup', function (e) {
    var c = e.keyCode; if (!held[c]) { return; } held[c] = false; run(ups, c, e);
  });
  w.addEventListener('blur', function () {
    Object.keys(held).forEach(function (c) { if (held[c]) { held[c] = false; run(ups, c, {}); } });
  });
  var bind = {
    key: function (k, onDown, onUp) {
      var c = code(k);
      if (onDown) { add(downs, c, onDown); }
      if (onUp) { add(ups, c, onUp); }
    },
    axis: function (left, right, down, up, callback) {
      if (typeof callback !== 'function') { return false; }
      var axis = [0, 0], stacks = [[], []];
      function recompute(i) { var s = stacks[i]; axis[i] = s.length ? s[s.length - 1] : 0; }
      function dir(k, i, v) {
        bind.key(k, function () { stacks[i].push(v); recompute(i); }, function () {
          var s = stacks[i], j = s.lastIndexOf(v); if (j !== -1) { s.splice(j, 1); } recompute(i);
        });
      }
      dir(up, 0, -1); dir(down, 0, 1); dir(left, 1, -1); dir(right, 1, 1);
      setInterval(function () { if (axis[0] === 0 && axis[1] === 0) { return; } callback(axis); }, 1);
    }
  };
  w.KeyboardJS = { bind: bind };
})(window);
