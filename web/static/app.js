"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHTML = (value) =>
  String(value ?? "—").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (value, digits = 0) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("es", { maximumFractionDigits: digits });
async function api(url, data) {
  const response = await fetch(
    url,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": $('meta[name="csrf-token"]').content,
          },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "No se pudo completar la solicitud.");
  return result;
}
function table(columns, rows) {
  if (!rows.length) return '<p class="empty">No hay filas para mostrar.</p>';
  return `<div class="table-wrap"><table><thead><tr>${columns.map((c) => `<th>${escapeHTML(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v) => `<td>${escapeHTML(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function records(rows) {
  const keys = Object.keys(rows[0] || {});
  return table(
    keys,
    rows.map((r) => keys.map((k) => r[k])),
  );
}
function kpis(selector, items) {
  $(selector).innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="kpi"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`,
    )
    .join("");
}
function tabs(selector) {
  const buttons = $$(selector + " button");
  buttons.forEach((button, index) => {
    button.type = "button";
    button.id = `tab-${button.dataset.tab}`;
    button.setAttribute("aria-controls", button.dataset.tab);
    const panel = $("#" + button.dataset.tab);
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", button.id);
    const activate = () => {
      buttons.forEach((b) => {
        const active = b === button;
        b.classList.toggle("selected", active);
        b.setAttribute("aria-selected", active);
        b.tabIndex = active ? 0 : -1;
        $("#" + b.dataset.tab).hidden = !active;
      });
      window.dispatchEvent(new Event("resize"));
    };
    button.addEventListener("click", activate);
    button.addEventListener("keydown", (e) => {
      if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
        e.preventDefault();
        const next =
          buttons[
            (index + (e.key === "ArrowRight" ? 1 : -1) + buttons.length) %
              buttons.length
          ];
        next.click();
        next.focus();
      }
    });
    button.setAttribute("aria-selected", button.classList.contains("selected"));
    button.tabIndex = button.classList.contains("selected") ? 0 : -1;
  });
}
$("#menu").addEventListener("click", () => {
  const open = $("#sidebar").classList.toggle("open");
  $("#menu").setAttribute("aria-expanded", open);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    $("#sidebar").classList.remove("open");
    $("#menu").setAttribute("aria-expanded", "false");
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#sidebar,#menu")) {
    $("#sidebar").classList.remove("open");
    $("#menu").setAttribute("aria-expanded", "false");
  }
});
let pollGeneration = 0;
async function watchJob(id, onProgress, onComplete, onError) {
  const generation = ++pollGeneration;
  while (generation === pollGeneration) {
    try {
      const job = await api(`/api/jobs/${id}`);
      if (job.status === "failed") throw new Error(job.error);
      onProgress(job);
      if (job.status === "completed") {
        onComplete(job.result);
        return;
      }
    } catch (error) {
      onError(error);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}
const questionData = $("#question-data")
  ? JSON.parse($("#question-data").textContent)
  : [];
if ($("#demo-form")) {
  let source = "bank",
    result = null;
  const form = $("#demo-form"),
    status = $("#demo-status"),
    submit = form.querySelector('[type="submit"]');
  $$("[data-source]").forEach((button) =>
    button.addEventListener("click", () => {
      source = button.dataset.source;
      $$("[data-source]").forEach((b) =>
        b.classList.toggle("selected", b === button),
      );
      $("#bank-input").hidden = source !== "bank";
      $("#free-input").hidden = source !== "free";
    }),
  );
  $("#operation").addEventListener("change", () => {
    const select = $("#case-select");
    select.replaceChildren(
      ...questionData
        .filter(
          (c) =>
            !$("#operation").value || c.operacion === $("#operation").value,
        )
        .map((c) => new Option(`${c.id} · ${c.pregunta}`, c.id)),
    );
  });
  function renderResult(value) {
    result = value;
    const trace = value.trace;
    $("#demo-result").hidden = false;
    $("#result-question").textContent =
      `${trace.question} · ${trace.quantization_id} · Esquema ${trace.schema_id}`;
    const sqlError = trace.validation_error || trace.execution_error;
    const failed = Boolean(sqlError) || trace.status.startsWith("error_");
    const milliseconds = (value, digits = 0) =>
      value == null ? "—" : `${number(value, digits)} ms`;
    const outcome = failed
      ? "Error SQL"
      : trace.execution_accuracy == null
        ? "Sin referencia"
        : trace.execution_accuracy ? "Correcta" : "Diferente";
    kpis("#demo-kpis", [
      ["Resultado", trace.status === "error_inferencia" ? "Error del modelo" : outcome],
      ["Inferencia", milliseconds(trace.inference_ms)],
      ["Ejecución SQL", milliseconds(trace.execution_ms, 2)],
      ["Filas obtenidas", number(trace.rows_returned)],
    ]);
    $("#sql-code").textContent =
      trace.final_sql || trace.initial_sql || "No se generó SQL válido.";
    $("#sql-error").hidden = !sqlError;
    $("#sql-error-detail").textContent = sqlError || "";
    $("#verdict").className = failed ? "alert" : "notice";
    $("#verdict").textContent = failed
      ? trace.status === "error_inferencia"
        ? "El modelo no pudo generar la consulta."
        : "El SQL generado no pudo ejecutarse. No hay datos obtenidos."
      : trace.execution_accuracy === 1
        ? "Los datos obtenidos coinciden con los esperados."
        : trace.execution_accuracy === 0
          ? "La consulta se ejecutó, pero los datos difieren de los esperados."
          : "Consulta ejecutada sin referencia de comparación.";
    const preview = (columns, rows, total) =>
      table(columns || [], rows || []) +
      (total > 100 ? '<p class="muted">Se muestran las primeras 100 filas.</p>' : "");
    $("#rows").innerHTML = failed
      ? '<p class="empty">Sin datos: la consulta generada contiene un error.</p>'
      : preview(value.columns, value.rows, trace.rows_returned);
    $("#gold-code").textContent = value.gold_sql || "Sin referencia para esta pregunta libre.";
    $("#gold-rows").innerHTML = value.gold_sql
      ? value.gold_rows_data == null
        ? '<p class="empty">La referencia no pudo ejecutarse.</p>'
        : preview(value.gold_columns, value.gold_rows_data, trace.gold_rows)
      : '<p class="empty">Sin datos esperados para esta pregunta libre.</p>';
    $("#demo-result").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function startWatch(id) {
    submit.disabled = true;
    $("#demo-result").hidden = true;
    result = null;
    status.hidden = false;
    status.className = "status-box";
    status.textContent = "Generando SQL y consultando los datos…";
    watchJob(
      id,
      () => {},
      (value) => {
        submit.disabled = false;
        status.hidden = true;
        sessionStorage.removeItem("sql-lab-demo-job");
        renderResult(value);
      },
      (error) => {
        submit.disabled = false;
        status.className = "alert";
        status.textContent = error.message;
        sessionStorage.removeItem("sql-lab-demo-job");
      },
    );
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submit.disabled = true;
    status.hidden = false;
    status.className = "status-box";
    status.textContent = "Preparando consulta…";
    const data = Object.fromEntries(new FormData(form));
    data.save = form.elements.save.checked;
    if (source === "free") delete data.case_id;
    else delete data.question;
    try {
      const job = await api("/api/demo", data);
      sessionStorage.setItem("sql-lab-demo-job", job.id);
      startWatch(job.id);
    } catch (error) {
      status.className = "alert";
      status.textContent = error.message;
      submit.disabled = false;
    }
  });
  $("#clear-demo").addEventListener("click", () => {
    $("#demo-result").hidden = true;
    if (!submit.disabled) status.hidden = true;
    result = null;
  });
  $("#download-trace").addEventListener("click", () => {
    if (!result) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result.trace, null, 2)], {
        type: "application/json",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "consulta.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("#load-schema").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    $("#schema-view").textContent = "Cargando esquema…";
    try {
      const data = await api(`/api/schema/${form.elements.schema.value}`);
      $("#schema-view").innerHTML =
        `<p class="muted">${data.metrics.tablas} tablas · ${data.metrics.relaciones} relaciones</p>` +
        Object.entries(data.tables)
          .map(
            ([name, info]) =>
              `<details class="panel"><summary>${escapeHTML(name)} · ${info.columnas.length} columnas</summary>${records(info.columnas)}</details>`,
          )
          .join("") +
        `<details><summary>DDL completo</summary><pre>${escapeHTML(data.ddl)}</pre></details>`;
    } catch (error) {
      $("#schema-view").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  const pending = sessionStorage.getItem("sql-lab-demo-job");
  if (pending) startWatch(pending);
}
if ($("#experiment-form")) {
  const form = $("#experiment-form"),
    submit = form.querySelector('[type="submit"]');
  let currentJob = null;
  const selected = (name) => [...new FormData(form).getAll(name)];
  function caseIds() {
    const mode = $("#case-mode").value;
    return mode === "manual"
      ? selected("cases")
      : questionData
          .slice(
            0,
            mode === "all"
              ? questionData.length
              : Number($("#pilot-count").value),
          )
          .map((c) => c.id);
  }
  function update() {
    const mode = $("#case-mode").value;
    $("#pilot-field").hidden = mode !== "pilot";
    $("#manual-cases").hidden = mode !== "manual";
    const qs = selected("quantizations"),
      ss = selected("schemas"),
      count = caseIds().length * Number(form.elements.repetitions.value);
    $("#total-attempts").textContent = qs.length * ss.length * count;
    $("#matrix").innerHTML = table(
      ["Cuantización", "Esquema", "Intentos"],
      qs.flatMap((q) => ss.map((s) => [q, s, count])),
    );
  }
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  update();
  function failure(error) {
    submit.disabled = false;
    $("#experiment-status").hidden = false;
    $("#experiment-message").textContent = error.message;
    $("#stop-experiment").disabled = true;
    sessionStorage.removeItem("sql-lab-experiment-job");
  }
  function watch(id) {
    currentJob = id;
    sessionStorage.setItem("sql-lab-experiment-job", id);
    submit.disabled = true;
    $("#experiment-status").hidden = false;
    $("#stop-experiment").disabled = false;
    $("#experiment-message").textContent =
      "Validando entorno y preparando el manifiesto…";
    watchJob(
      id,
      (job) => {
        const p = job.progress;
        if (p.total) {
          $("#experiment-progress").value = p.done / p.total;
          $("#experiment-message").textContent =
            `${p.done} de ${p.total} intentos · ${p.last_case_id} · ${p.last_status}`;
        }
      },
      (result) => {
        submit.disabled = false;
        $("#stop-experiment").disabled = true;
        $("#experiment-results").href = `/resultados?run=${encodeURIComponent(result.run_id)}`;
        $("#experiment-message").textContent =
          `Corrida ${result.run_id}: ${result.completed}/${result.total} intentos. ${result.status === "paused" ? "Detenida; puedes reanudarla desde Corridas guardadas." : "Finalizada."}`;
        sessionStorage.removeItem("sql-lab-experiment-job");
      },
      failure,
    );
    $("#experiment-status").scrollIntoView({ behavior: "smooth" });
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submit.disabled = true;
    const data = Object.fromEntries(new FormData(form));
    Object.assign(data, {
      quantizations: selected("quantizations"),
      schemas: selected("schemas"),
      cases: caseIds(),
      repetitions: Number(data.repetitions),
      seed: Number(data.seed),
      shuffle: form.elements.shuffle.checked,
    });
    try {
      watch((await api("/api/experiments", data)).id);
    } catch (error) {
      failure(error);
    }
  });
  $$("[data-resume]").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        watch(
          (
            await api(
              `/api/resume/${encodeURIComponent(button.dataset.resume)}`,
              {},
            )
          ).id,
        );
      } catch (error) {
        failure(error);
      } finally {
        button.disabled = false;
      }
    }),
  );
  $("#stop-experiment").addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try {
      $("#experiment-message").textContent = (
        await api(`/api/jobs/${currentJob}/stop`, {})
      ).message;
    } catch (error) {
      failure(error);
    }
  });
  const pending = sessionStorage.getItem("sql-lab-experiment-job");
  if (pending) watch(pending);
}
if ($("#result-filters")) {
  const filters = $("#result-filters");
  let generation = 0;
  const charts = $$("#charts .chart");
  function chartTheme() {
    const style = getComputedStyle(document.documentElement);
    const color = (name) => style.getPropertyValue(`--color-${name}`).trim();
    return {
      "font.color": color("ink"),
      "paper_bgcolor": "transparent",
      "plot_bgcolor": color("field"),
      "xaxis.gridcolor": color("edge"),
      "yaxis.gridcolor": color("edge"),
      "xaxis.zerolinecolor": color("control"),
      "yaxis.zerolinecolor": color("control"),
      "modebar.color": color("muted"),
      "modebar.activecolor": color("accent"),
    };
  }
  document.addEventListener("lab:themechange", () => {
    const theme = chartTheme();
    for (const chart of charts) {
      if (chart.layout) Plotly.relayout(chart, theme);
    }
  });
  async function load() {
    const revision = ++generation;
    const params = new URLSearchParams(new FormData(filters));
    // Conserva la selección experimental inicial; cada corrida incluye su propio modo.
    const mode = filters.elements.run.value
      ? filters.elements.run.selectedOptions[0].dataset.mode
      : "strict";
    params.set("mode", mode);
    $("#results-status").textContent = "Cargando resultados…";
    for (const kind of ["csv", "json", "md"])
      $("#export-" + kind).href = `/api/export/${kind}?${params}`;
    try {
      const data = await api("/api/results?" + params);
      if (revision !== generation) return;
      const k = data.kpis;
      kpis("#result-kpis", [
        ["Intentos guardados", number(k.n_attempts)],
        [
          "Exactitud",
          k.accuracy_pct == null ? "—" : number(k.accuracy_pct, 1) + " %",
        ],
        ["Inferencia mediana", number(k.median_inference_ms) + " ms"],
        ["Errores SQL", number(k.sql_errors)],
      ]);
      $("#results-status").textContent = data.attempts.length
        ? ""
        : "Sin intentos para estos filtros. Ejecuta una consulta o un experimento para comenzar.";
      $("#factorial").innerHTML = records(data.summary);
      $("#errors").innerHTML = records(data.errors);
      $("#attempts").innerHTML =
        data.attempts
          .map(
            (a) =>
              `<details class="panel"><summary>${escapeHTML(a.case_id)} · ${escapeHTML(a.quantization_id)} · ${escapeHTML(a.schema_id)} · Exactitud: ${a.accuracy ?? "Sin referencia"}</summary><pre>${escapeHTML(a.initial_sql || a.validation_error || a.execution_error)}</pre><details><summary>Traza completa</summary><pre>${escapeHTML(JSON.stringify(JSON.parse(a.trace_json), null, 2))}</pre></details></details>`,
          )
          .join("") || '<p class="empty">Sin intentos guardados.</p>';
      // Plotly accepts dotted paths in relayout. For new plots, apply the same colors to the layout.
      const theme = chartTheme();
      await Promise.all(data.charts.map((figure, index) => {
        const layout = figure.layout;
        const axes = {
          gridcolor: theme["xaxis.gridcolor"],
          zerolinecolor: theme["xaxis.zerolinecolor"],
        };
        return Plotly.react(charts[index], figure.data, {
          ...layout,
          font: { ...layout.font, color: theme["font.color"] },
          xaxis: { ...layout.xaxis, ...axes },
          yaxis: { ...layout.yaxis, ...axes },
          modebar: { color: theme["modebar.color"], activecolor: theme["modebar.activecolor"] },
          paper_bgcolor: theme.paper_bgcolor,
          plot_bgcolor: theme.plot_bgcolor,
          autosize: true,
        }, { responsive: true, displaylogo: false });
      }));
    } catch (error) {
      $("#results-status").textContent = error.message;
    }
  }
  filters.addEventListener("submit", (e) => e.preventDefault());
  filters.addEventListener("change", load);
  tabs("#results-tabs");
  load();
}
