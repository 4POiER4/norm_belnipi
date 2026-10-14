(function () {
  "use strict";

  const app = document.getElementById("admin-app");
  const userLabel = document.getElementById("admin-user-label");
  const btnLogout = document.getElementById("btn-logout");

  let state = {
    bootstrap: null,
    tab: "ad",
    setupStep: 0,
    message: "",
    messageType: "",
    sitesEditing: null,
    sitesAdding: false,
  };

  async function api(path, opts) {
    const timeoutMs = (opts && typeof opts.timeout_ms === "number") ? opts.timeout_ms : 60000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch("/admin/api" + path, {
      headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
      credentials: "same-origin",
      signal: controller.signal,
      ...opts,
    });
    clearTimeout(timer);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.message || "Ошибка запроса");
    return data;
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function showMsg(text, type) {
    state.message = text;
    state.messageType = type || "ok";
    render();
  }

  function confirmAdmin(text) {
    return new Promise(resolve => {
      const overlay = document.createElement("div");
      overlay.className = "admin-modal-overlay";
      overlay.innerHTML = `
        <div class="admin-modal" role="dialog" aria-modal="true">
          <h3>Подтверждение</h3>
          <p>${esc(text)}</p>
          <div class="admin-modal-actions">
            <button type="button" class="btn btn-ghost btn-sm" data-action="cancel">Отмена</button>
            <button type="button" class="btn btn-primary btn-sm" data-action="ok">Удалить</button>
          </div>
        </div>
      `;
      overlay.addEventListener("click", e => {
        if (e.target === overlay) {
          overlay.remove();
          resolve(false);
        }
      });
      overlay.querySelector('[data-action="cancel"]').addEventListener("click", () => {
        overlay.remove();
        resolve(false);
      });
      overlay.querySelector('[data-action="ok"]').addEventListener("click", () => {
        overlay.remove();
        resolve(true);
      });
      document.body.appendChild(overlay);
    });
  }

  function field(id, label, value, opts) {
    opts = opts || {};
    const type = opts.type || "text";
    const hint = opts.hint ? `<div class="hint">${opts.hint}</div>` : "";
    const ph = opts.placeholder ? ` placeholder="${esc(opts.placeholder)}"` : "";
    const req = opts.required ? " required" : "";
    return `
      <div class="admin-field">
        <label for="${id}">${esc(label)}</label>
        <input id="${id}" type="${type}" value="${esc(value)}"${ph}${req}${opts.autocomplete ? ` autocomplete="${opts.autocomplete}"` : ""}>
        ${hint}
      </div>`;
  }

  function renderWizardSteps(current) {
    const steps = [
      { n: 1, label: "Начало" },
      { n: 2, label: "Active Directory" },
    ];
    return `
      <div class="admin-wizard-steps">
        ${steps
          .map(function (s) {
            const cls = s.n < current ? "done" : s.n === current ? "active" : "";
            return `<div class="admin-wizard-step ${cls}"><span class="admin-wizard-num">${s.n}</span><span>${s.label}</span></div>`;
          })
          .join("")}
      </div>`;
  }

  function renderWelcome() {
    return `
      <div class="admin-wizard-wrap">
        ${renderWizardSteps(1)}
        <div class="admin-card">
          <span class="admin-badge">Первый запуск</span>
          <h2>Настройка для вашей организации</h2>
          <p class="admin-desc">Сначала подключите Active Directory — через него будут входить администраторы этой панели. Интеграции с внешними сайтами (Стройдок и др.) можно добавить позже во вкладке «Интеграции»; они сразу используются основным приложением при проверке нормативов.</p>
          <ul class="admin-checklist">
            <li><strong>Шаг 1.</strong> Укажите параметры AD и список администраторов панели.</li>
            <li><strong>Шаг 2.</strong> Войдите под своей учётной записью AD и при необходимости добавьте сайты с логином и паролем.</li>
          </ul>
          <div class="admin-actions">
            <button type="button" class="btn btn-primary btn-sm" id="btn-wizard-start">Начать настройку →</button>
          </div>
        </div>
      </div>`;
  }

  function renderSetupAd() {
    return `
      <div class="admin-wizard-wrap">
        ${renderWizardSteps(2)}
        <div class="admin-card">
          <h2>Active Directory</h2>
          <p class="admin-desc">Укажите параметры вашего домена. Подключение проверяется перед сохранением.</p>
          ${state.message ? `<div class="admin-msg ${state.messageType}">${esc(state.message)}</div>` : ""}
          <form id="setup-ad-form" class="admin-form-grid">
            ${field("ad-uri", "Адрес контроллера (LDAP URI)", "", {
              placeholder: "ldap://192.168.0.4",
              required: true,
              hint: "Один адрес: ldap://IP или ldap://имя_сервера",
            })}
            ${field("ad-base", "Base DN", "", {
              placeholder: "DC=company,DC=local",
              required: true,
              hint: "Корень дерева каталога вашей организации",
            })}
            ${field("ad-bind", "Служебная учётная запись (Bind DN)", "", {
              placeholder: "CN=svc-ldap,OU=IT,DC=company,DC=local",
              required: true,
              hint: "Учётная запись с правом читать каталог пользователей",
            })}
            ${field("ad-bind-pass", "Пароль служебной учётки", "", {
              type: "password",
              required: true,
              autocomplete: "new-password",
            })}
            ${field("ad-user-attr", "Поле логина пользователя", "sAMAccountName", {
              hint: "Если не указано — используется sAMAccountName (Windows AD)",
            })}
            ${field("ad-admins", "Администраторы панели", "", {
              placeholder: "ivanov, petrov",
              required: true,
              hint: "Логины AD через запятую",
            })}
            ${field("ad-admin-group", "Группа AD с правами администратора", "", {
              placeholder: "IT-Admins",
              hint: "Необязательно — имя группы безопасности AD",
            })}
            <div class="admin-actions">
              <button type="button" class="btn btn-ghost btn-sm" id="btn-wizard-back">← Назад</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btn-test-ad">Проверить AD</button>
              <button type="submit" class="btn btn-primary btn-sm">Завершить настройку</button>
            </div>
          </form>
        </div>
      </div>`;
  }

  function setupHeaders() {
    return { "Content-Type": "application/json" };
  }

  function collectAdFromForm(prefix) {
    prefix = prefix || "ad-";
    return {
      uri: document.getElementById(prefix + "uri").value.trim(),
      base_dn: document.getElementById(prefix + "base").value.trim(),
      bind_dn: document.getElementById(prefix + "bind").value.trim(),
      bind_password: document.getElementById(prefix + "bind-pass").value,
      user_attr: document.getElementById(prefix + "user-attr").value.trim() || "sAMAccountName",
      admin_users: document.getElementById(prefix + "admins").value.trim(),
      admin_group: document.getElementById(prefix + "admin-group").value.trim(),
    };
  }

  function bindWelcome() {
    document.getElementById("btn-wizard-start").addEventListener("click", function () {
      state.setupStep = 2;
      state.message = "";
      render();
    });
  }

  function bindSetupAd() {
    document.getElementById("btn-wizard-back").addEventListener("click", function () {
      state.setupStep = 1;
      state.message = "";
      render();
    });
    document.getElementById("btn-test-ad").addEventListener("click", async function () {
      try {
        const res = await fetch("/admin/api/settings/ad/test", {
          method: "POST",
          headers: setupHeaders(),
          credentials: "same-origin",
          body: JSON.stringify(collectAdFromForm("ad-")),
        });
        const data = await res.json();
        showMsg(data.message || (data.success ? "Подключение успешно" : "Ошибка"), data.success ? "ok" : "err");
      } catch (e) {
        showMsg(e.message, "err");
      }
    });
    document.getElementById("setup-ad-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      try {
        const ad = collectAdFromForm("ad-");
        const resTest = await fetch("/admin/api/settings/ad/test", {
          method: "POST",
          headers: setupHeaders(),
          credentials: "same-origin",
          body: JSON.stringify(ad),
        });
        const testData = await resTest.json();
        if (!resTest.ok || !testData.success) throw new Error(testData.message || testData.error || "AD не отвечает");

        const payload = {
          admin_users: ad.admin_users,
          ad: ad,
        };
        const res = await fetch("/admin/api/setup", {
          method: "POST",
          headers: setupHeaders(),
          credentials: "same-origin",
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Не удалось сохранить");
        state.message = data.message || "Готово! Войдите под своей учётной записью AD.";
        state.messageType = "ok";
        state.bootstrap.ad_configured = true;
        state.setupStep = 0;
        setTimeout(load, 600);
      } catch (err) {
        showMsg(err.message, "err");
      }
    });
  }

  function renderLogin() {
    return `
      <div class="admin-login-wrap">
        <div class="admin-card">
          <h2>Вход администратора</h2>
          <p class="admin-desc">Используйте логин и пароль вашей учётной записи Active Directory</p>
          ${state.message ? `<div class="admin-msg ${state.messageType}">${esc(state.message)}</div>` : ""}
          <form id="login-form" class="admin-form-grid">
            ${field("login-user", "Логин", "", { placeholder: "ivanov", autocomplete: "username", required: true })}
            ${field("login-pass", "Пароль", "", { type: "password", autocomplete: "current-password", required: true })}
            <div class="admin-actions">
              <button type="submit" class="btn btn-primary btn-sm">Войти</button>
            </div>
          </form>
        </div>
      </div>`;
  }

  function renderAdPanel(ad) {
    ad = ad || {};
    return `
      <div class="admin-card" id="panel-ad">
        <h2>Active Directory</h2>
        <p class="admin-desc">Параметры подключения к домену вашей организации.</p>
        <form id="ad-form" class="admin-form-grid">
          ${field("ad-uri", "LDAP URI", ad.uri || "", { required: true })}
          ${field("ad-base", "Base DN", ad.base_dn || "", { required: true })}
          ${field("ad-bind", "Bind DN", ad.bind_dn || "", { required: true })}
          ${field("ad-bind-pass", "Пароль Bind DN", "", {
            type: "password",
            hint: ad.bind_password_set ? "Сохранён — оставьте пустым, чтобы не менять" : "Обязателен при первом сохранении",
            autocomplete: "new-password",
          })}
          ${field("ad-user-attr", "Атрибут логина", ad.user_attr || "sAMAccountName")}
          ${field("ad-admins", "Администраторы", ad.admin_users || "", { hint: "Логины AD через запятую" })}
          ${field("ad-admin-group", "Группа AD", ad.admin_group || "")}
          <div class="admin-actions">
            <button type="button" class="btn btn-ghost btn-sm" id="btn-test-ad">Проверить</button>
            <button type="submit" class="btn btn-primary btn-sm">Сохранить</button>
          </div>
        </form>
      </div>`;
  }

  function renderSiteForm(site, opts) {
    opts = opts || {};
    const prefix = opts.prefix || "site-";
    const isNew = !site || !site.id;
    site = site || {};
    return `
      <form class="site-form admin-form-grid" data-site-id="${esc(site.id || "")}">
        ${field(prefix + "name", "Название", site.name || "", {
          placeholder: "NormaCS или Стройдок (normy.stn.by)",
          required: true,
          hint: "Произвольное имя для отображения в списке",
        })}
        ${field(prefix + "url", "Адрес сайта", site.site_url || "", {
          placeholder: "http://n.nanocad.ru:9999/normacsweb/",
          required: true,
          hint: "Полный URL веб-интерфейса Стройдока или NormaCS, включая http:// или https://",
        })}
        ${field(prefix + "login", "Логин", site.login || "", { hint: "Учётная запись Стройдока или NormaCS" })}
        ${field(prefix + "pass", "Пароль", "", {
          type: "password",
          hint: isNew ? "Пароль хранится в базе в зашифрованном виде" : site.password_set ? "Сохранён — пусто = не менять" : "Не задан",
          autocomplete: "new-password",
        })}
        <div class="admin-actions">
          ${opts.showCancel ? `<button type="button" class="btn btn-ghost btn-sm btn-site-cancel">Отмена</button>` : ""}
          ${!isNew && site.can_test ? `<button type="button" class="btn btn-ghost btn-sm btn-site-test" data-id="${esc(site.id)}">Проверить</button>` : ""}
          ${!isNew ? `<button type="button" class="btn btn-ghost btn-sm btn-site-delete" data-id="${esc(site.id)}">Удалить</button>` : ""}
          <button type="submit" class="btn btn-primary btn-sm">${isNew ? "Добавить" : "Сохранить"}</button>
        </div>
      </form>`;
  }

  function renderSitesPanel(sites) {
    sites = sites || [];
    const addBlock = state.sitesAdding
      ? `<div class="admin-card admin-card-accent">${renderSiteForm(null, { prefix: "new-site-", showCancel: true })}</div>`
      : "";

    const list =
      sites.length === 0 && !state.sitesAdding
        ? `<p class="admin-desc">Пока нет подключённых сайтов. Добавьте Стройдок и NormaCS для проверки нормативов.</p>`
        : sites
            .map(function (site) {
              if (state.sitesEditing === site.id) {
                return `<div class="admin-card">${renderSiteForm(site, { prefix: "edit-" + site.id + "-" })}</div>`;
              }
              return `
          <div class="admin-card admin-site-card">
            <div class="admin-site-head">
              <h2>${esc(site.name)}</h2>
              ${site.kind === "stn"
                ? '<span class="admin-site-tag">Стройдок</span>'
                : site.kind === "normacs"
                  ? '<span class="admin-site-tag">NormaCS</span>'
                : site.kind === "tnpa"
                  ? '<span class="admin-site-tag">ТНПА</span>'
                  : ""}
            </div>
            <p class="admin-desc admin-site-url">${esc(site.site_url)}</p>
            <div class="admin-site-meta">
              <span>Логин: <strong>${site.login ? esc(site.login) : "—"}</strong></span>
              <span>Пароль: <strong>${site.password_set ? "задан" : "не задан"}</strong></span>
            </div>
            <div class="admin-actions">
              ${site.can_test ? `<button type="button" class="btn btn-ghost btn-sm btn-site-test" data-id="${esc(site.id)}">Проверить</button>` : ""}
              <button type="button" class="btn btn-ghost btn-sm btn-site-edit" data-id="${esc(site.id)}">Изменить</button>
            </div>
          </div>`;
            })
            .join("");

    return `
      <div class="admin-card">
        <div class="admin-site-toolbar">
          <div>
            <h2>Внешние сайты</h2>
            <p class="admin-desc" style="margin-bottom:0">Проверка нормативов: Стройдок (РБ) и NormaCS (РФ).</p>
          </div>
          ${!state.sitesAdding ? `<button type="button" class="btn btn-primary btn-sm" id="btn-add-site">+ Добавить сайт</button>` : ""}
        </div>
      </div>
      ${addBlock}
      ${list}`;
  }

  function renderDashboard(ad, sites) {
    const tabs = [
      { id: "ad", label: "Active Directory" },
      { id: "integrations", label: "Интеграции" },
    ];
    return `
      ${state.message ? `<div class="admin-msg ${state.messageType}">${esc(state.message)}</div>` : ""}
      <div class="admin-tabs">${tabs
        .map(function (t) {
          return `<button type="button" class="admin-tab${state.tab === t.id ? " active" : ""}" data-tab="${t.id}">${t.label}</button>`;
        })
        .join("")}</div>
      ${state.tab === "ad" ? renderAdPanel(ad) : renderSitesPanel(sites)}`;
  }

  function bindLoginForm() {
    document.getElementById("login-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      try {
        await api("/login", {
          method: "POST",
          body: JSON.stringify({
            username: document.getElementById("login-user").value,
            password: document.getElementById("login-pass").value,
          }),
        });
        state.message = "";
        await load();
      } catch (err) {
        showMsg(err.message, "err");
      }
    });
  }

  function collectSiteFromForm(prefix) {
    return {
      name: document.getElementById(prefix + "name").value.trim(),
      site_url: document.getElementById(prefix + "url").value.trim(),
      login: document.getElementById(prefix + "login").value.trim(),
      password: document.getElementById(prefix + "pass").value,
    };
  }

  function bindSitesPanel() {
    const addBtn = document.getElementById("btn-add-site");
    if (addBtn) {
      addBtn.addEventListener("click", function () {
        state.sitesAdding = true;
        state.sitesEditing = null;
        render();
        bindDashboard(state._ad, state._sites);
      });
    }

    document.querySelectorAll(".btn-site-cancel").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.sitesAdding = false;
        state.sitesEditing = null;
        render();
        bindDashboard(state._ad, state._sites);
      });
    });

    document.querySelectorAll(".btn-site-edit").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.sitesEditing = btn.dataset.id;
        state.sitesAdding = false;
        render();
        bindDashboard(state._ad, state._sites);
      });
    });

    document.querySelectorAll(".btn-site-delete").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        if (btn.disabled) return;
        btn.disabled = true;
        const ok = await confirmAdmin("Удалить этот сайт?");
        if (!ok) {
          btn.disabled = false;
          return;
        }
        try {
          await api("/settings/sites/" + btn.dataset.id, { method: "DELETE", body: "{}", timeout_ms: 60000 });
          state.sitesEditing = null;
          showMsg("Удалено", "ok");
          await load();
        } catch (e) {
          showMsg(e.message, "err");
        } finally {
          btn.disabled = false;
        }
      });
    });

    document.querySelectorAll(".btn-site-test").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        if (btn.disabled) return;
        btn.disabled = true;
        try {
          const data = await api("/settings/sites/" + btn.dataset.id + "/test", { method: "POST", body: "{}", timeout_ms: 60000 });
          showMsg(data.message || "OK", "ok");
        } catch (e) {
          showMsg(e.message, "err");
        } finally {
          btn.disabled = false;
        }
      });
    });

    const newForm = document.querySelector('.site-form[data-site-id=""]');
    if (newForm) {
      newForm.addEventListener("submit", async function (e) {
        e.preventDefault();
        try {
          await api("/settings/sites", { method: "POST", body: JSON.stringify(collectSiteFromForm("new-site-")) });
          state.sitesAdding = false;
          showMsg("Сайт добавлен", "ok");
          await load();
        } catch (err) {
          showMsg(err.message, "err");
        }
      });
    }

    document.querySelectorAll(".site-form").forEach(function (form) {
      const siteId = form.dataset.siteId;
      if (!siteId) return;
      form.addEventListener("submit", async function (e) {
        e.preventDefault();
        const prefix = "edit-" + siteId + "-";
        try {
          await api("/settings/sites/" + siteId, {
            method: "PUT",
            body: JSON.stringify(collectSiteFromForm(prefix)),
          });
          state.sitesEditing = null;
          showMsg("Сохранено", "ok");
          await load();
        } catch (err) {
          showMsg(err.message, "err");
        }
      });
    });
  }

  function bindDashboard(ad, sites) {
    document.querySelectorAll(".admin-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.tab = btn.dataset.tab;
        state.sitesAdding = false;
        state.sitesEditing = null;
        render();
        bindDashboard(state._ad, state._sites);
      });
    });
    const adForm = document.getElementById("ad-form");
    if (adForm) {
      document.getElementById("btn-test-ad").addEventListener("click", async function () {
        try {
          const data = await api("/settings/ad/test", { method: "POST", body: JSON.stringify(collectAdFromForm("ad-")) });
          showMsg(data.message, "ok");
        } catch (e) {
          showMsg(e.message, "err");
        }
      });
      adForm.addEventListener("submit", async function (e) {
        e.preventDefault();
        try {
          await api("/settings/ad", { method: "PUT", body: JSON.stringify(collectAdFromForm("ad-")) });
          showMsg("Сохранено", "ok");
        } catch (err) {
          showMsg(err.message, "err");
        }
      });
    }
    if (state.tab === "integrations") bindSitesPanel();
  }

  function render() {
    const b = state.bootstrap;
    if (!b) {
      app.innerHTML = '<div class="admin-loading">Загрузка…</div>';
      return;
    }

    if (!b.ad_configured) {
      userLabel.textContent = "";
      btnLogout.hidden = true;
      if (state.setupStep === 0 || state.setupStep === 1) {
        app.innerHTML = renderWelcome();
        bindWelcome();
      } else {
        app.innerHTML = renderSetupAd();
        bindSetupAd();
      }
      return;
    }

    if (!b.logged_in) {
      app.innerHTML = renderLogin();
      bindLoginForm();
      userLabel.textContent = "";
      btnLogout.hidden = true;
      return;
    }

    app.innerHTML = renderDashboard(state._ad, state._sites);
    bindDashboard(state._ad, state._sites);
    userLabel.textContent = b.display_name || b.username || "";
    btnLogout.hidden = false;
  }

  async function load() {
    try {
      state.bootstrap = await api("/bootstrap");
      if (!state.bootstrap.ad_configured) state.setupStep = state.setupStep || 1;
      if (state.bootstrap.logged_in) {
        state._ad = await api("/settings/ad");
        const sitesData = await api("/settings/sites");
        state._sites = sitesData.sites || [];
        state.bootstrap.display_name = (await api("/me")).display_name;
      }
    } catch (e) {
      state.message = e.message;
      state.messageType = "err";
    }
    render();
  }

  btnLogout.addEventListener("click", async function () {
    await api("/logout", { method: "POST", body: "{}" });
    state.bootstrap = { ad_configured: true, logged_in: false, setup_allowed: false };
    state.message = "";
    render();
  });

  load();
})();
