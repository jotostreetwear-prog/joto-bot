/* =========================================================================
   JOTO UI Shell — каркас в стиле Supabase Dashboard.
   Внедряет на каждую страницу:
   - верхнюю панель (~46px) с хлебными крошками:
     ⚡ / JOTO [FREE] / article-generator / <страница> [PRODUCTION] ⌄
   - левый икон-рейл (48px), разворачивающийся при наведении в панель
     с подписями (overlay), активный пункт подсвечен.
   Стили — в /static/indexowb.css (секция «КАРКАС SUPABASE»).
   ========================================================================= */
(function () {
  "use strict";
  var d = document;

  /* Спека снята со светлой темы — фиксируем её до отработки скриптов страниц. */
  d.documentElement.classList.add("sb-shell");
  d.documentElement.setAttribute("data-theme", "light");
  try { localStorage.setItem("theme", "light"); } catch (e) {}

  function icon(paths, viewBox) {
    return '<svg viewBox="' + (viewBox || "0 0 24 24") + '" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths + "</svg>";
  }

  var ICONS = {
    zap: icon('<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>'),
    tag: icon('<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><circle cx="7" cy="7" r="1.5"/>'),
    table: icon('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>'),
    chart: icon('<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>')
  };

  var PAGES = [
    { path: "/",         key: "cards",  name: "Карточки WB",         icon: ICONS.table, group: 1,
      isActive: function (p) { return p === "/" || p === "" || p.indexOf("/cards") === 0; } },
    { path: "/articles", key: "app",    name: "Генерация артикулов", icon: ICONS.tag,   group: 2,
      isActive: function (p) { return p.indexOf("/articles") === 0; } },
    { path: "/season",   key: "season", name: "Сезонная аналитика",  icon: ICONS.chart, group: 2,
      isActive: function (p) { return p.indexOf("/season") === 0; } }
  ];

  var path = location.pathname || "/";
  var current = null;
  for (var i = 0; i < PAGES.length; i++) {
    if (PAGES[i].isActive(path)) { current = PAGES[i]; break; }
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function buildShell() {
    /* повторная инициализация (например, из bfcache) не нужна */
    if (d.querySelector(".sb-topbar")) return;

    /* ---------- Top bar ---------- */
    var topbar = d.createElement("header");
    topbar.className = "sb-topbar";
    topbar.innerHTML =
      '<div class="sb-crumbs">' +
        '<span class="sb-logo">' + ICONS.zap + "</span>" +
        '<span class="sb-crumb">JOTO <span class="sb-badge is-free">Free</span></span>' +
        '<span class="sb-slash">/</span>' +
        '<span class="sb-crumb">article-generator</span>' +
        '<span class="sb-slash">/</span>' +
        '<button class="sb-crumb" id="sbPageCrumb" type="button" aria-haspopup="menu" aria-expanded="false">' +
          esc(current ? current.name : "Разделы") +
          ' <span class="sb-badge is-prod">Production</span>' +
          '<svg class="sb-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>' +
        "</button>" +
      "</div>" +
      '<div class="sb-top-right">' +
        '<span class="sb-status"><span class="sb-dot"></span>online</span>' +
        '<span class="sb-avatar">J</span>' +
      "</div>";

    /* Dropdown переключателя разделов */
    var menu = d.createElement("nav");
    menu.className = "sb-menu";
    menu.hidden = true;
    menu.setAttribute("role", "menu");
    menu.innerHTML = PAGES.map(function (p) {
      var active = current && current.key === p.key;
      return '<a role="menuitem" href="' + p.path + '">' + p.icon +
        "<span>" + esc(p.name) + "</span>" +
        (active ? '<span class="sb-check">✓</span>' : "") + "</a>";
    }).join("");

    /* ---------- Icon rail ---------- */
    var rail = d.createElement("nav");
    rail.className = "sb-rail";
    rail.setAttribute("aria-label", "Разделы");
    var html = "", prevGroup = null;
    PAGES.forEach(function (p) {
      if (prevGroup !== null && p.group !== prevGroup) html += '<div class="sb-rail-sep"></div>';
      prevGroup = p.group;
      var active = current && current.key === p.key;
      html += '<a href="' + p.path + '"' + (active ? ' class="active" aria-current="page"' : "") +
        ' title="' + esc(p.name) + '">' + p.icon + '<span class="sb-label">' + esc(p.name) + "</span></a>";
    });
    html += '<div class="sb-rail-spacer"></div>';
    rail.innerHTML = html;

    d.body.appendChild(topbar);
    d.body.appendChild(menu);
    d.body.appendChild(rail);

    /* ---------- Поведение dropdown ---------- */
    var crumb = topbar.querySelector("#sbPageCrumb");
    function closeMenu() {
      menu.hidden = true;
      crumb.setAttribute("aria-expanded", "false");
    }
    crumb.addEventListener("click", function (e) {
      e.stopPropagation();
      if (menu.hidden) {
        var r = crumb.getBoundingClientRect();
        menu.style.top = (r.bottom + 6) + "px";
        menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 246)) + "px";
        menu.hidden = false;
        crumb.setAttribute("aria-expanded", "true");
      } else {
        closeMenu();
      }
    });
    d.addEventListener("click", function (e) {
      if (!menu.hidden && !menu.contains(e.target)) closeMenu();
    });
    d.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMenu();
    });

    /* ---------- Косметика легаси-разметки ---------- */
    /* Эмодзи в подписях вкладок — убираем (минимализм Supabase). */
    var tabs = d.querySelectorAll(".tab");
    for (var t = 0; t < tabs.length; t++) {
      var node = tabs[t].firstChild;
      if (node && node.nodeType === 3) {
        node.nodeValue = node.nodeValue.replace(/^[^\wа-яёА-ЯЁ]+\s*/u, "");
      }
    }

    /* Скрипты страниц могли восстановить тёмную тему из localStorage — фиксируем светлую. */
    d.documentElement.setAttribute("data-theme", "light");
  }

  if (d.readyState === "loading") {
    d.addEventListener("DOMContentLoaded", buildShell);
  } else {
    buildShell();
  }
})();
