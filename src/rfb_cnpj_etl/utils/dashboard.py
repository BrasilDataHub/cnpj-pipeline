# utils/dashboard.py

"""
Read-only web dashboard for pipeline progress.

It is a **pure view**: it reads the state JSON on every request and has no
business logic, never writes to the state, never touches the database. If
the dashboard goes down, the pipeline does not notice.

Runs on a daemon thread (so it never keeps the process alive after the load)
and uses only `http.server` from the standard library. The page uses
**Alpine.js via CDN** — the script is downloaded by the visitor's browser,
not by the ETL machine, which only has to serve the HTML.

Protected by **HTTP Basic Auth**, the simplest mechanism there is: the
browser shows its native prompt, no login page to maintain. When no password
is provided, one is generated and printed to the log — the dashboard never
comes up open by accident (`--no-auth` disables it deliberately).
"""

import base64
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .logger import print_log
from ..config import DASHBOARD_REFRESH_SECONDS, DASHBOARD_USER

# Alpine.js 3 (current stable line). The `@3` range lets jsDelivr serve the
# latest 3.x — to pin an exact version, replace with `alpinejs@3.14.1`.
ALPINE_CDN = "https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"

PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Internal, ephemeral panel: no indexing whatsoever. The server also sends
     X-Robots-Tag, which covers /state.json (where no meta tag fits). -->
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<meta name="referrer" content="no-referrer">
<!-- Tells the browser the page supports both themes: form controls and
     scrollbars follow the system scheme. -->
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f6f7f9" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0e1116" media="(prefers-color-scheme: dark)">
<title>CNPJ Pipeline — andamento</title>
<!-- Inline favicon (SVG data URI): avoids the /favicon.ico 404 in the log. -->
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle cx='8' cy='8' r='7' fill='%2315803d'/%3E%3C/svg%3E">
<script defer src="__ALPINE__"></script>
<style>
  :root {
    --bg:#f6f7f9; --card:#fff; --fg:#1a1d21; --muted:#6b7280; --line:#e5e7eb;
    --ok:#15803d; --okbg:#dcfce7; --run:#1d4ed8; --runbg:#dbeafe;
    /* --idle is the "pending" badge text over --idlebg: #6b7280 would give
       4.39:1, below the 4.5:1 WCAG 2.1 AA minimum for small text. */
    --err:#b91c1c; --errbg:#fee2e2; --idle:#656c7a; --idlebg:#f3f4f6;
    --warn:#92400e; --warnbg:#fef3c7;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0e1116; --card:#161b22; --fg:#e6edf3; --muted:#8b949e; --line:#30363d;
      --ok:#3fb950; --okbg:#12261a; --run:#58a6ff; --runbg:#0d2135;
      --err:#f85149; --errbg:#2d1214; --idle:#8b949e; --idlebg:#1c2128;
      --warn:#f0b429; --warnbg:#2a2205;
    }
  }
  * { box-sizing:border-box; }
  [x-cloak] { display:none !important; }
  /* Screen-reader-only content (labels the layout can do without). */
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
             overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
  :focus-visible { outline:2px solid var(--run); outline-offset:2px;
                   border-radius:4px; }
  /* WCAG 2.3.3: reduced-motion users get no pulse and no transitions. */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation:none !important; transition:none !important; }
  }
  body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg);
         font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
  .wrap { max-width:900px; margin:0 auto; }
  header { display:flex; align-items:flex-start; justify-content:space-between;
           gap:1rem; flex-wrap:wrap; margin-bottom:1.5rem; }
  h1 { font-size:1.35rem; margin:0 0 .25rem; letter-spacing:-.01em; }
  .sub { color:var(--muted); font-size:.85rem; margin:0; }
  .controls { display:flex; align-items:center; gap:.5rem; font-size:.8rem;
              color:var(--muted); }
  select { font:inherit; font-size:.8rem; padding:.25rem .5rem; color:var(--fg);
           background:var(--card); border:1px solid var(--line); border-radius:6px; }
  a { color:inherit; }
  .dotlive { width:7px; height:7px; border-radius:50%; background:var(--ok);
             display:inline-block; }
  .dotlive.off { background:var(--idle); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:1.15rem 1.25rem; margin-bottom:1rem; }
  .row { display:flex; flex-wrap:wrap; gap:1.5rem; margin:0; }
  .metric { flex:1; min-width:130px; }
  .metric dt { color:var(--muted); font-size:.75rem; text-transform:uppercase;
               letter-spacing:.04em; }
  .metric dd { margin:.15rem 0 0; font-size:1.3rem; font-weight:600;
               font-variant-numeric:tabular-nums; }
  .badge { display:inline-block; padding:.2rem .6rem; border-radius:999px;
           font-size:.75rem; font-weight:600; text-transform:uppercase;
           letter-spacing:.03em; }
  .b-success,.b-completed { color:var(--ok); background:var(--okbg); }
  .b-running,.b-in_progress { color:var(--run); background:var(--runbg); }
  .b-failed { color:var(--err); background:var(--errbg); }
  .b-pending { color:var(--idle); background:var(--idlebg); }
  h2 { font-size:.8rem; text-transform:uppercase; letter-spacing:.05em;
       color:var(--muted); margin:0 0 .85rem; font-weight:600; }
  ol { list-style:none; margin:0; padding:0; }
  /* Grid instead of flex: the status and duration columns stay aligned
     across steps instead of floating with the text width. */
  li { display:grid; grid-template-columns:9px minmax(0,1fr) 6.5rem 5rem;
       column-gap:.85rem; align-items:start; padding:.55rem 0;
       border-bottom:1px solid var(--line); }
  li:last-child { border-bottom:0; }
  @media (max-width:520px) {
    li { grid-template-columns:9px minmax(0,1fr) auto; }
    li .dur { grid-column:2 / -1; justify-self:start; }
  }
  .dot { width:9px; height:9px; border-radius:50%; margin-top:.45rem; }
  .d-success { background:var(--ok); }
  .d-running { background:var(--run); animation:pulse 1.4s ease-in-out infinite; }
  .d-failed  { background:var(--err); }
  .d-pending { background:var(--line); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .step { flex:1; min-width:0; }
  /* These paragraphs are structure, not prose: without the browser's default
     margin, which would space the step list too far apart. */
  .step p { margin:0; }
  .step .name { font-weight:500; }
  .step .meta { color:var(--muted); font-size:.8rem; margin-top:.1rem;
                font-variant-numeric:tabular-nums; }
  .dur { color:var(--muted); font-size:.85rem; font-variant-numeric:tabular-nums;
         white-space:nowrap; justify-self:end; }
  .badge-col { justify-self:start; }
  /* The error spans from the text column to the end, so it is not squeezed. */
  .err { grid-column:2 / -1; margin:.45rem 0 .15rem; padding:.5rem .65rem;
         background:var(--errbg); color:var(--err); border-radius:6px;
         font-size:.8rem; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
         white-space:pre-wrap; word-break:break-word; }
  /* Non-fatal loss (dead-letter): amber, same layout as .err. */
  .lost { grid-column:2 / -1; margin:.45rem 0 .15rem; padding:.5rem .65rem;
          background:var(--warnbg); color:var(--warn); border-radius:6px;
          font-size:.8rem; white-space:pre-wrap; word-break:break-word; }
  /* Progress inside a running step (load, download). */
  .subprog { margin-top:.35rem; }
  .subprog progress { height:3px; margin-top:.25rem; }
  .env { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
         gap:.5rem 1.5rem; margin:0; padding:0; list-style:none; }
  .env li { display:flex; gap:.5rem; justify-content:space-between; padding:0 0 .2rem;
            border-bottom:1px dotted var(--line); }
  .env .rot { color:var(--muted); font-size:.8rem; }
  .env .val { font-size:.8rem; text-align:right; word-break:break-all;
              font-variant-numeric:tabular-nums; }
  .foot { color:var(--muted); font-size:.75rem; text-align:center; margin-top:1.5rem; }
  .notice { color:var(--muted); font-style:italic; }
  .noscript-card { max-width:900px; margin:0 auto; }
  /* Native <progress>: accessibility comes for free (the browser exposes
     value/max). `appearance:none` is what allows styling it. */
  progress { appearance:none; -webkit-appearance:none; display:block; width:100%;
             height:4px; margin-top:.9rem; border:0; border-radius:999px;
             overflow:hidden; background:var(--line); color:var(--ok); }
  progress::-webkit-progress-bar { background:var(--line); }
  progress::-webkit-progress-value { background:var(--ok); transition:width .4s ease; }
  progress::-moz-progress-bar { background:var(--ok); }
</style>
</head>
<body>
<noscript>
  <div class="card noscript-card">
    <strong>Esta página precisa de JavaScript.</strong>
    Os dados brutos continuam disponíveis em <a href="state.json">state.json</a>.
  </div>
</noscript>

<main class="wrap" x-data="dash()" x-init="start()">

  <header>
    <div>
      <h1>CNPJ Pipeline</h1>
      <p class="sub" x-text="subtitle()">carregando…</p>
    </div>
    <div class="controls">
      <span class="dotlive" :class="{'off': interval === 0}" aria-hidden="true"></span>
      <span x-text="interval ? 'ao vivo' : 'pausado'">ao vivo</span>
      <label for="interval" class="sr-only">Intervalo de atualização</label>
      <select id="interval" x-model.number="interval" @change="reschedule()">
        <option value="3">3s</option>
        <option value="6">6s</option>
        <option value="10">10s</option>
        <option value="30">30s</option>
        <option value="0">pausar</option>
      </select>
    </div>
  </header>

  <!-- Without Alpine (blocked CDN) nothing below renders; this notice shows
       up after a few seconds so the page is not left blank. -->
  <div id="nojs" class="card notice" hidden>
    Não foi possível carregar a biblioteca de interface (Alpine.js via CDN).
    Os dados continuam em <a href="state.json">state.json</a>.
  </div>

  <template x-if="error">
    <div class="card notice" role="alert" x-text="error"></div>
  </template>

  <template x-if="st">
    <div x-cloak>
      <section class="card" aria-labelledby="h-summary" aria-live="polite" aria-atomic="true">
        <h2 id="h-summary" class="sr-only">Resumo da execução</h2>
        <dl class="row">
          <div class="metric"><dt>Status</dt>
            <dd><span class="badge" :class="'b-'+st.status" x-text="statusLabel(st.status)"></span></dd></div>
          <div class="metric"><dt>Etapas</dt>
            <dd x-text="completedSteps()+'/'+st.steps.length"></dd></div>
          <div class="metric"><dt>Decorrido</dt>
            <dd x-text="elapsedTotal()"></dd></div>
          <div class="metric"><dt>Arquivos</dt>
            <dd x-text="num(metaSum('files_downloaded'))"></dd></div>
          <div class="metric"><dt>Registros</dt>
            <dd x-text="num(metaSum('records_inserted'))"></dd></div>
        </dl>
        <progress :value="completedSteps()" :max="st.steps.length"
                  :aria-label="'Progresso: '+completedSteps()+' de '+st.steps.length+' etapas concluídas'"
                  x-text="pct()+'%'"></progress>
      </section>

      <template x-if="runningStep()">
        <section class="card" aria-labelledby="h-now" aria-live="polite">
          <h2 id="h-now">Executando agora</h2>
          <div class="step">
            <p class="name" x-text="stepLabel(runningStep().name)"></p>
            <p class="meta" x-text="'iniciada às '+timeOf(runningStep().started_at)+' · '+dur(runningStep().started_at, null)"></p>
          </div>
        </section>
      </template>

      <section class="card" aria-labelledby="h-steps">
        <h2 id="h-steps">Etapas</h2>
        <ol>
          <template x-for="s in st.steps" :key="s.name">
            <li>
              <span class="dot" :class="'d-'+s.status" aria-hidden="true"></span>
              <div class="step">
                <p class="name" x-text="stepLabel(s.name)"></p>
                <p class="meta" x-show="metaLine(s)" x-text="metaLine(s)"></p>
                <!-- Inner progress: only meaningful while the step is running. -->
                <div class="subprog" x-show="s.status === 'running' && progressDetail(s)">
                  <p class="meta" x-text="progressDetail(s)"></p>
                  <progress x-show="s.metadata && s.metadata.percent != null"
                            :value="s.metadata ? s.metadata.percent : 0" max="100"
                            :aria-label="'Progresso de '+stepLabel(s.name)"></progress>
                </div>
              </div>
              <span class="badge badge-col" :class="'b-'+s.status" x-text="statusLabel(s.status)"></span>
              <span class="dur" x-text="stepDuration(s)"></span>
              <p class="err" x-show="s.error" x-text="s.error" role="note"
                 :aria-label="'Erro em '+stepLabel(s.name)"></p>
              <p class="lost" x-show="s.metadata && s.metadata.failed_batches"
                 x-text="deadLetterNote(s)" role="note"
                 :aria-label="'Lotes com falha em '+stepLabel(s.name)"></p>
            </li>
          </template>
        </ol>
      </section>

      <section class="card" aria-labelledby="h-env"
               x-show="st.environment || st.database" x-cloak>
        <h2 id="h-env">Ambiente</h2>
        <!-- A list, not <dl>: with `x-for` the pair lives inside <template>,
             and `dl > template > div > dt` is not a valid chain in static
             validation of the served file. -->
        <ul class="env">
          <template x-for="item in environmentRows()" :key="item[0]">
            <li><span class="rot" x-text="item[0]"></span><span class="val" x-text="item[1]"></span></li>
          </template>
        </ul>
      </section>
    </div>
  </template>

  <footer class="foot">
    <span x-text="interval ? ('atualiza a cada '+interval+'s') : 'atualização pausada'"></span>
    · somente leitura ·
    <a href="state.json">state.json</a>
    <span x-show="lastFetch" x-cloak> · última leitura às <span x-text="lastFetch"></span></span>
  </footer>
</main>

<script>
function dash() {
  return {
    st: null,
    error: null,
    timer: null,
    lastFetch: "",
    now: Date.now(),
    // The chosen interval persists across page reloads.
    interval: Number(localStorage.getItem("cnpj_refresh") ?? __REFRESH__),

    // Portuguese labels for the state's public (English) values.
    STEP_LABELS: {
      download: "Download dos arquivos",
      file_validation: "Validação dos arquivos",
      schema_init: "Criação do schema",
      data_load: "Carga de dados",
      patches: "Correções de dados",
      tables_logged: "Conversão para LOGGED",
      primary_keys: "Chaves primárias",
      indexes: "Índices",
      foreign_keys: "Chaves estrangeiras",
      search_table: "Tabela de busca",
      materialized_views: "Materialized views",
    },
    STATUS_LABELS: {
      pending: "pendente",
      running: "em execução",
      success: "concluída",
      failed: "falhou",
      in_progress: "em andamento",
      completed: "concluído",
    },
    META_LABELS: {
      records_inserted: "registros inseridos",
      files_downloaded: "arquivos baixados",
      total_bytes: "bytes baixados",
    },

    start() {
      this.fetchState(); this.reschedule();
      setInterval(() => this.now = Date.now(), 1000);
      // The tab title reflects progress — useful with several tabs open.
      this.$watch("st", () => { document.title = this.tabTitle(); });
    },

    stepLabel(name) { return this.STEP_LABELS[name] || name; },
    statusLabel(status) { return this.STATUS_LABELS[status] || status; },

    tabTitle() {
      if (!this.st) return "CNPJ Pipeline — andamento";
      return `${this.completedSteps()}/${this.st.steps.length} · ${this.statusLabel(this.st.status)} — CNPJ Pipeline`;
    },

    reschedule() {
      localStorage.setItem("cnpj_refresh", this.interval);
      if (this.timer) clearInterval(this.timer);
      if (this.interval > 0) {
        this.timer = setInterval(() => this.fetchState(), this.interval * 1000);
      }
    },

    async fetchState() {
      try {
        const r = await fetch("state.json", {cache: "no-store"});
        if (!r.ok) throw new Error("HTTP " + r.status);
        this.st = await r.json(); this.error = null;
        this.lastFetch = new Date().toLocaleTimeString("pt-BR");
      } catch (e) {
        this.error = "Não foi possível ler o estado: " + e.message;
      }
    },

    // ---- derived values
    completedSteps() { return this.st.steps.filter(s => s.status === "success").length; },
    pct() { return this.st.steps.length
              ? Math.round(this.completedSteps() / this.st.steps.length * 100) : 0; },
    runningStep() { return this.st.steps.find(s => s.status === "running"); },
    metaSum(key) {
      return this.st.steps.reduce((t, s) => t + ((s.metadata || {})[key] || 0), 0);
    },
    subtitle() {
      if (!this.st) return "carregando…";
      return `período de referência ${this.st.reference_period || "—"} · execução ${(this.st.run_id||"").slice(0,8)}`;
    },
    elapsedTotal() {
      const end = this.st.status === "in_progress" ? null : this.st.updated_at;
      return this.dur(this.st.created_at, end);
    },
    stepDuration(s) {
      if (s.started_at && s.finished_at) return this.dur(s.started_at, s.finished_at);
      if (s.status === "running") return this.dur(s.started_at, null);
      return "";
    },
    // Keys that describe live progress (shown by progressDetail, not on the
    // metadata line — otherwise the running step becomes a soup of fields).
    PROGRESS_KEYS: ["current_table", "current_file", "records_total", "percent",
                    "files_total", "files_remaining"],
    // Keys rendered by the dedicated dead-letter note, not on the metadata line.
    LOSS_KEYS: ["failed_batches", "failed_rows", "dead_letter_files"],

    metaLine(s) {
      if (!s.started_at) return "";
      let t = this.timeOf(s.started_at) + (s.finished_at ? " → " + this.timeOf(s.finished_at) : "");
      if ((s.attempts || 0) > 1) t += ` · ${s.attempts} tentativas`;
      const meta = s.metadata || {};
      const isRunning = s.status === "running";
      const extras = Object.entries(meta)
        .filter(([k]) => !this.PROGRESS_KEYS.includes(k) && !this.LOSS_KEYS.includes(k))
        .filter(([k]) => !(isRunning && (k === "files_downloaded" || k === "records_inserted")))
        .map(([k, v]) => `${this.META_LABELS[k] || k}: ${typeof v === "number" ? this.num(v) : v}`)
        .join(" · ");
      return extras ? t + " · " + extras : t;
    },

    // Non-fatal loss note: batches parked in the dead-letter directory.
    deadLetterNote(s) {
      const m = s.metadata || {};
      const files = (m.dead_letter_files || []).join(", ");
      return `⚠ ${this.num(m.failed_batches)} lote(s) com falha `
        + `(${this.num(m.failed_rows || 0)} linhas) preservados em dead-letter`
        + (files ? `: ${files}` : "")
        + ` — reprocesse com: python etl.py db dead-letter --retry`;
    },

    // Readable text of what is happening inside the running step.
    progressDetail(s) {
      const m = s.metadata || {};
      const parts = [];
      if (m.files_total) {
        parts.push(`${this.num(m.files_downloaded || 0)} de ${this.num(m.files_total)} arquivos`);
        if (m.files_remaining) parts.push(`${this.num(m.files_remaining)} restantes`);
      }
      if (m.records_total) {
        parts.push(`${this.num(m.records_inserted || 0)} de ${this.num(m.records_total)} registros`);
      }
      if (m.current_table) parts.push(`tabela ${m.current_table}`);
      if (m.current_file) parts.push(m.current_file);
      if (m.percent != null) parts.push(`${m.percent}%`);
      return parts.join(" · ");
    },

    // Environment and database flattened into label/value pairs.
    environmentRows() {
      const e = this.st.environment || {}, d = this.st.database || {};
      const rows = [];
      const add = (label, val) => { if (val !== null && val !== undefined && val !== "") rows.push([label, val]); };
      add("execução", e.runtime === "docker" ? "Docker" : "Python (direto)");
      add("container", e.container_id);
      add("orquestrador", e.orchestrator);
      add("máquina", e.hostname);
      add("IP", e.ip);
      add("sistema", e.os);
      add("arquitetura", e.arch);
      add("Python", e.python);
      add("CPUs", e.cpus);
      add("PID", e.pid);
      add("banco", d.database);
      add("servidor", d.host ? `${d.host}:${d.port ?? ""}` : null);
      add("usuário do banco", d.user);
      add("versão", d.version);
      add("tamanho", d.size);
      add("conexões", d.connections);
      if (d.reachable === false) add("banco", "inacessível");
      return rows;
    },

    // ---- formatting
    num(n) { return (n ?? 0).toLocaleString("pt-BR"); },
    timeOf(t) { return t ? new Date(t).toLocaleTimeString("pt-BR",
                 {hour:"2-digit", minute:"2-digit", second:"2-digit"}) : "—"; },
    dur(a, b) {
      if (!a) return "";
      // `now` is reactive (1s tick), so running steps count up on their own.
      const end = b ? new Date(b).getTime() : this.now;
      let s = Math.max(0, Math.round((end - new Date(a).getTime()) / 1000));
      const h = Math.floor(s/3600); s -= h*3600;
      const m = Math.floor(s/60); s -= m*60;
      if (h) return `${h}h ${String(m).padStart(2,"0")}m`;
      if (m) return `${m}m ${String(s).padStart(2,"0")}s`;
      return `${s}s`;
    },
  };
}

// Alpine sets window.Alpine on init. If it has not shown up after 4s, the CDN
// did not respond — show the notice instead of leaving the page blank.
setTimeout(function () {
  if (!window.Alpine) document.getElementById("nojs").hidden = false;
}, 4000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    state_path: Optional[Path] = None
    auth_user: Optional[str] = None
    auth_password: Optional[str] = None   # None => no authentication

    # -- authentication -------------------------------------------------------

    def _authenticated(self) -> bool:
        if not type(self).auth_password:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, password = base64.b64decode(
                header[6:].strip()).decode("utf-8").partition(":")
        except Exception:
            return False
        # compare_digest on both fields: constant-time comparison.
        user_ok = secrets.compare_digest(user, type(self).auth_user or "")
        password_ok = secrets.compare_digest(password, type(self).auth_password or "")
        return user_ok and password_ok

    def _request_credentials(self):
        body = "Acesso restrito ao dashboard do pipeline.\n".encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="CNPJ Pipeline", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    # -- routes --------------------------------------------------------------

    def do_GET(self):  # noqa: N802 (stdlib signature)
        if not self._authenticated():
            self._request_credentials()
            return

        route = self.path.split("?")[0].rstrip("/") or "/"
        if route in ("/", "/index.html"):
            body = (PAGE
                    .replace("__ALPINE__", ALPINE_CDN)
                    .replace("__REFRESH__", str(DASHBOARD_REFRESH_SECONDS)))
            self._respond(200, "text/html; charset=utf-8", body.encode("utf-8"))
        elif route in ("/state.json", "/state"):
            self._respond_state()
        else:
            self._respond(404, "text/plain; charset=utf-8", "não encontrado".encode("utf-8"))

    def _respond_state(self):
        path = type(self).state_path
        if not path or not Path(path).exists():
            self._respond(404, "application/json",
                          '{"error":"estado ainda não criado"}'.encode("utf-8"))
            return
        try:
            payload = Path(path).read_bytes()
            json.loads(payload)   # never serve JSON truncated by a concurrent write
        except (OSError, json.JSONDecodeError):
            self._respond(503, "application/json",
                          '{"error":"estado indisponível no momento"}'.encode("utf-8"))
            return
        self._respond(200, "application/json; charset=utf-8", payload)

    def _respond(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # X-Robots-Tag also covers /state.json, where no <meta> tag fits.
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass   # tab closed mid-response

    def log_message(self, *args):
        """Silences the access log — it would pollute the load log."""
        return


def generate_password() -> str:
    """Short, readable password for a temporary dashboard session."""
    return secrets.token_urlsafe(9)


def start_dashboard(
        state_path: Path,
        port: int,
        host: str = "127.0.0.1",
        password: Optional[str] = None,
        user: str = DASHBOARD_USER,
        auth: bool = True,
):
    """Starts the dashboard on a daemon thread. Returns the server, or None on failure.

    Never raises: a busy port must not prevent the load from running.

    :param password: Basic Auth password. When `auth` is on and no password is
                     given, one is generated and shown in the log.
    :param auth: `False` disables authentication (`--no-auth`).
    """
    resolved_password = None
    if auth:
        resolved_password = password or generate_password()

    handler = type("Handler", (_Handler,), {
        "state_path": Path(state_path),
        "auth_user": user,
        "auth_password": resolved_password,
    })

    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print_log(
            f"DASHBOARD NÃO PÔDE SUBIR EM {host}:{port} ({exc}) — pipeline segue normalmente",
            level="warning"
        )
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="dashboard")
    thread.start()

    display_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    actual_port = server.server_address[1]   # with port=0 the OS picks a free one
    print_log(f"DASHBOARD DISPONÍVEL EM http://{display_host}:{actual_port}", level="web")

    if resolved_password:
        print_log(f"  -> usuário: {user}   senha: {resolved_password}", level="search")
        if not password:
            print_log("  -> senha gerada automaticamente; defina --dashboard-password "
                      "para escolher a sua", level="docs")
    else:
        print_log("  -> SEM AUTENTICAÇÃO (--no-auth): qualquer um com acesso à porta "
                  "vê o andamento", level="warning")

    if host == "127.0.0.1":
        print_log("  -> ouvindo apenas em 127.0.0.1; use --host 0.0.0.0 para acessar "
                  "de fora do container", level="docs")
    return server
