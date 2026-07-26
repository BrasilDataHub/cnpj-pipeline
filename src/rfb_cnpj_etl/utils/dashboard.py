# utils/dashboard.py

"""
Dashboard web somente leitura do andamento do pipeline.

É uma **view pura**: lê o JSON de estado a cada requisição e não tem nenhuma
lógica de negócio, nem escreve no estado, nem toca no banco. Se o dashboard
cair, o pipeline não percebe.

Roda numa thread daemon (para não segurar o processo ao fim da carga) e usa
apenas `http.server` da biblioteca padrão. A página usa **Alpine.js via CDN**
— quem baixa o script é o navegador de quem acessa, não a máquina do ETL, que
só precisa servir o HTML.

Protegido por **HTTP Basic Auth**, o mecanismo mais simples que existe: o
navegador exibe o prompt nativo, sem página de login para manter. Se nenhuma
senha for informada, uma é gerada e impressa no log — o dashboard nunca sobe
aberto por acidente (`--no-auth` desliga conscientemente).
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

# Alpine.js 3 (linha estável atual). O range `@3` deixa o jsDelivr servir a
# última 3.x — para fixar uma versão exata, troque por `alpinejs@3.14.1`.
ALPINE_CDN = "https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"

PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Painel interno e efêmero: fora com qualquer indexação. O servidor também
     manda X-Robots-Tag, que cobre o /state.json (onde não há como pôr meta). -->
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<meta name="referrer" content="no-referrer">
<!-- Informa ao navegador que a página tem os dois temas: controles de
     formulário e barras de rolagem acompanham o esquema do sistema. -->
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f6f7f9" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0e1116" media="(prefers-color-scheme: dark)">
<title>CNPJ Pipeline — andamento</title>
<!-- Favicon embutido (SVG em data URI): evita o 404 de /favicon.ico no log. -->
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle cx='8' cy='8' r='7' fill='%2315803d'/%3E%3C/svg%3E">
<script defer src="__ALPINE__"></script>
<style>
  :root {
    --bg:#f6f7f9; --card:#fff; --fg:#1a1d21; --muted:#6b7280; --line:#e5e7eb;
    --ok:#15803d; --okbg:#dcfce7; --run:#1d4ed8; --runbg:#dbeafe;
    /* --idle é o texto do badge "pending" sobre --idlebg: #6b7280 daria
       4.39:1, abaixo do mínimo de 4.5:1 da WCAG 2.1 AA para texto pequeno. */
    --err:#b91c1c; --errbg:#fee2e2; --idle:#656c7a; --idlebg:#f3f4f6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0e1116; --card:#161b22; --fg:#e6edf3; --muted:#8b949e; --line:#30363d;
      --ok:#3fb950; --okbg:#12261a; --run:#58a6ff; --runbg:#0d2135;
      --err:#f85149; --errbg:#2d1214; --idle:#8b949e; --idlebg:#1c2128;
    }
  }
  * { box-sizing:border-box; }
  [x-cloak] { display:none !important; }
  /* Conteúdo só para leitores de tela (rótulos que a diagramação dispensa). */
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
             overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
  :focus-visible { outline:2px solid var(--run); outline-offset:2px;
                   border-radius:4px; }
  /* WCAG 2.3.3: quem pede menos movimento não recebe pulso nem transição. */
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
  /* Grid em vez de flex: as colunas de status e duração ficam alinhadas
     entre todas as etapas, em vez de flutuarem com o tamanho do texto. */
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
  /* Os parágrafos aqui são estrutura, não prosa: sem a margem padrão do
     navegador, que espaçaria demais a lista de etapas. */
  .step p { margin:0; }
  .step .name { font-weight:500; }
  .step .meta { color:var(--muted); font-size:.8rem; margin-top:.1rem;
                font-variant-numeric:tabular-nums; }
  .dur { color:var(--muted); font-size:.85rem; font-variant-numeric:tabular-nums;
         white-space:nowrap; justify-self:end; }
  .badge-col { justify-self:start; }
  /* O erro atravessa da coluna do texto até o fim, para não ficar espremido. */
  .err { grid-column:2 / -1; margin:.45rem 0 .15rem; padding:.5rem .65rem;
         background:var(--errbg); color:var(--err); border-radius:6px;
         font-size:.8rem; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
         white-space:pre-wrap; word-break:break-word; }
  /* Progresso dentro de uma etapa em andamento (carga, download). */
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
  .aviso { color:var(--muted); font-style:italic; }
  .noscript-card { max-width:900px; margin:0 auto; }
  /* <progress> nativo: acessibilidade vem de graça (o navegador expõe
     valor/máximo). `appearance:none` é o que permite estilizar. */
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
      <p class="sub" x-text="legenda()">carregando…</p>
    </div>
    <div class="controls">
      <span class="dotlive" :class="{'off': intervalo === 0}" aria-hidden="true"></span>
      <span x-text="intervalo ? 'ao vivo' : 'pausado'">ao vivo</span>
      <label for="intervalo" class="sr-only">Intervalo de atualização</label>
      <select id="intervalo" x-model.number="intervalo" @change="reagendar()">
        <option value="3">3s</option>
        <option value="6">6s</option>
        <option value="10">10s</option>
        <option value="30">30s</option>
        <option value="0">pausar</option>
      </select>
    </div>
  </header>

  <!-- Sem Alpine (CDN bloqueado) nada abaixo renderiza; este aviso aparece
       depois de alguns segundos para a página não ficar em branco. -->
  <div id="semjs" class="card aviso" hidden>
    Não foi possível carregar a biblioteca de interface (Alpine.js via CDN).
    Os dados continuam em <a href="state.json">state.json</a>.
  </div>

  <template x-if="erro">
    <div class="card aviso" role="alert" x-text="erro"></div>
  </template>

  <template x-if="st">
    <div x-cloak>
      <section class="card" aria-labelledby="h-resumo" aria-live="polite" aria-atomic="true">
        <h2 id="h-resumo" class="sr-only">Resumo da execução</h2>
        <dl class="row">
          <div class="metric"><dt>Status</dt>
            <dd><span class="badge" :class="'b-'+st.status" x-text="st.status"></span></dd></div>
          <div class="metric"><dt>Etapas</dt>
            <dd x-text="concluidas()+'/'+st.steps.length"></dd></div>
          <div class="metric"><dt>Decorrido</dt>
            <dd x-text="decorrido()"></dd></div>
          <div class="metric"><dt>Arquivos</dt>
            <dd x-text="num(soma('files_downloaded'))"></dd></div>
          <div class="metric"><dt>Registros</dt>
            <dd x-text="num(soma('records_inserted'))"></dd></div>
        </dl>
        <progress :value="concluidas()" :max="st.steps.length"
                  :aria-label="'Progresso: '+concluidas()+' de '+st.steps.length+' etapas concluídas'"
                  x-text="pct()+'%'"></progress>
      </section>

      <template x-if="rodando()">
        <section class="card" aria-labelledby="h-agora" aria-live="polite">
          <h2 id="h-agora">Executando agora</h2>
          <div class="step">
            <p class="name" x-text="rodando().name"></p>
            <p class="meta" x-text="'iniciada às '+hora(rodando().started_at)+' · '+dur(rodando().started_at, null)"></p>
          </div>
        </section>
      </template>

      <section class="card" aria-labelledby="h-etapas">
        <h2 id="h-etapas">Etapas</h2>
        <ol>
          <template x-for="s in st.steps" :key="s.name">
            <li>
              <span class="dot" :class="'d-'+s.status" aria-hidden="true"></span>
              <div class="step">
                <p class="name" x-text="s.name"></p>
                <p class="meta" x-show="linhaMeta(s)" x-text="linhaMeta(s)"></p>
                <!-- Progresso interno: só faz sentido enquanto a etapa corre. -->
                <div class="subprog" x-show="s.status === 'running' && detalheProgresso(s)">
                  <p class="meta" x-text="detalheProgresso(s)"></p>
                  <progress x-show="s.metadata && s.metadata.percentual != null"
                            :value="s.metadata ? s.metadata.percentual : 0" max="100"
                            :aria-label="'Progresso de '+s.name"></progress>
                </div>
              </div>
              <span class="badge badge-col" :class="'b-'+s.status" x-text="s.status"></span>
              <span class="dur" x-text="duracaoEtapa(s)"></span>
              <p class="err" x-show="s.error" x-text="s.error" role="note"
                 :aria-label="'Erro em '+s.name"></p>
            </li>
          </template>
        </ol>
      </section>

      <section class="card" aria-labelledby="h-ambiente"
               x-show="st.environment || st.database" x-cloak>
        <h2 id="h-ambiente">Ambiente</h2>
        <!-- Lista, e não <dl>: com `x-for` o par vive dentro de <template>,
             e `dl > template > div > dt` não é cadeia válida na validação
             estática do arquivo servido. -->
        <ul class="env">
          <template x-for="item in linhasAmbiente()" :key="item[0]">
            <li><span class="rot" x-text="item[0]"></span><span class="val" x-text="item[1]"></span></li>
          </template>
        </ul>
      </section>
    </div>
  </template>

  <footer class="foot">
    <span x-text="intervalo ? ('atualiza a cada '+intervalo+'s') : 'atualização pausada'"></span>
    · somente leitura ·
    <a href="state.json">state.json</a>
    <span x-show="ultima" x-cloak> · última leitura às <span x-text="ultima"></span></span>
  </footer>
</main>

<script>
function dash() {
  return {
    st: null,
    erro: null,
    timer: null,
    ultima: "",
    agora: Date.now(),
    // O intervalo escolhido persiste entre recargas da página.
    intervalo: Number(localStorage.getItem("cnpj_refresh") ?? __REFRESH__),

    start() {
      this.buscar(); this.reagendar();
      setInterval(() => this.agora = Date.now(), 1000);
      // Título da aba reflete o andamento — útil com várias abas abertas.
      this.$watch("st", () => { document.title = this.tituloAba(); });
    },

    tituloAba() {
      if (!this.st) return "CNPJ Pipeline — andamento";
      return `${this.concluidas()}/${this.st.steps.length} · ${this.st.status} — CNPJ Pipeline`;
    },

    reagendar() {
      localStorage.setItem("cnpj_refresh", this.intervalo);
      if (this.timer) clearInterval(this.timer);
      if (this.intervalo > 0) {
        this.timer = setInterval(() => this.buscar(), this.intervalo * 1000);
      }
    },

    async buscar() {
      try {
        const r = await fetch("state.json", {cache: "no-store"});
        if (!r.ok) throw new Error("HTTP " + r.status);
        this.st = await r.json(); this.erro = null;
        this.ultima = new Date().toLocaleTimeString("pt-BR");
      } catch (e) {
        this.erro = "Não foi possível ler o estado: " + e.message;
      }
    },

    // ---- derivados
    concluidas() { return this.st.steps.filter(s => s.status === "success").length; },
    pct() { return this.st.steps.length
              ? Math.round(this.concluidas() / this.st.steps.length * 100) : 0; },
    rodando() { return this.st.steps.find(s => s.status === "running"); },
    soma(chave) {
      return this.st.steps.reduce((t, s) => t + ((s.metadata || {})[chave] || 0), 0);
    },
    legenda() {
      if (!this.st) return "carregando…";
      return `período de referência ${this.st.reference_period || "—"} · run ${(this.st.run_id||"").slice(0,8)}`;
    },
    decorrido() {
      const fim = this.st.status === "in_progress" ? null : this.st.updated_at;
      return this.dur(this.st.created_at, fim);
    },
    duracaoEtapa(s) {
      if (s.started_at && s.finished_at) return this.dur(s.started_at, s.finished_at);
      if (s.status === "running") return this.dur(s.started_at, null);
      return "";
    },
    // Chaves que descrevem o andamento (mostradas por detalheProgresso, não
    // na linha de metadados — senão a etapa em curso viraria uma sopa de campos).
    PROGRESSO: ["tabela_atual", "arquivo_atual", "records_total", "percentual",
                "arquivos_baixados", "arquivos_total", "arquivos_restantes"],

    linhaMeta(s) {
      if (!s.started_at) return "";
      let t = this.hora(s.started_at) + (s.finished_at ? " → " + this.hora(s.finished_at) : "");
      if ((s.attempts || 0) > 1) t += ` · ${s.attempts} tentativas`;
      const extras = Object.entries(s.metadata || {})
        .filter(([k]) => !this.PROGRESSO.includes(k))
        .map(([k, v]) => `${k}: ${typeof v === "number" ? this.num(v) : v}`).join(" · ");
      return extras ? t + " · " + extras : t;
    },

    // Texto legível do que está acontecendo dentro da etapa em execução.
    detalheProgresso(s) {
      const m = s.metadata || {};
      const partes = [];
      if (m.arquivos_total) {
        partes.push(`${this.num(m.arquivos_baixados || 0)} de ${this.num(m.arquivos_total)} arquivos`);
        if (m.arquivos_restantes) partes.push(`${this.num(m.arquivos_restantes)} restantes`);
      }
      if (m.records_total) {
        partes.push(`${this.num(m.records_inserted || 0)} de ${this.num(m.records_total)} registros`);
      }
      if (m.tabela_atual) partes.push(`tabela ${m.tabela_atual}`);
      if (m.arquivo_atual) partes.push(m.arquivo_atual);
      if (m.percentual != null) partes.push(`${m.percentual}%`);
      return partes.join(" · ");
    },

    // Ambiente e banco achatados em pares rótulo/valor para o <dl>.
    linhasAmbiente() {
      const e = this.st.environment || {}, d = this.st.database || {};
      const linhas = [];
      const add = (rot, val) => { if (val !== null && val !== undefined && val !== "") linhas.push([rot, val]); };
      add("execução", e.runtime === "docker" ? "Docker" : "Python (direto)");
      add("container", e.container_id);
      add("orquestrador", e.orquestrador);
      add("máquina", e.hostname);
      add("IP", e.ip);
      add("sistema", e.so);
      add("arquitetura", e.arquitetura);
      add("Python", e.python);
      add("CPUs", e.cpus);
      add("PID", e.pid);
      add("banco", d.database);
      add("servidor", d.host ? `${d.host}:${d.porta ?? ""}` : null);
      add("usuário do banco", d.usuario);
      add("versão", d.versao);
      add("tamanho", d.tamanho);
      add("conexões", d.conexoes);
      if (d.acessivel === false) add("banco", "inacessível");
      return linhas;
    },

    // ---- formatação
    num(n) { return (n ?? 0).toLocaleString("pt-BR"); },
    hora(t) { return t ? new Date(t).toLocaleTimeString("pt-BR",
                 {hour:"2-digit", minute:"2-digit", second:"2-digit"}) : "—"; },
    dur(a, b) {
      if (!a) return "";
      // `agora` é reativo (tick de 1s), então etapas em curso contam sozinhas.
      const fim = b ? new Date(b).getTime() : this.agora;
      let s = Math.max(0, Math.round((fim - new Date(a).getTime()) / 1000));
      const h = Math.floor(s/3600); s -= h*3600;
      const m = Math.floor(s/60); s -= m*60;
      if (h) return `${h}h ${String(m).padStart(2,"0")}m`;
      if (m) return `${m}m ${String(s).padStart(2,"0")}s`;
      return `${s}s`;
    },
  };
}

// Alpine define window.Alpine ao inicializar. Se em 4s não apareceu, o CDN
// não respondeu — mostra o aviso em vez de deixar a página vazia.
setTimeout(function () {
  if (!window.Alpine) document.getElementById("semjs").hidden = false;
}, 4000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    state_path: Optional[Path] = None
    auth_user: Optional[str] = None
    auth_password: Optional[str] = None   # None => sem autenticação

    # -- autenticação -------------------------------------------------------

    def _autenticado(self) -> bool:
        if not type(self).auth_password:
            return True
        cabecalho = self.headers.get("Authorization", "")
        if not cabecalho.startswith("Basic "):
            return False
        try:
            usuario, _, senha = base64.b64decode(
                cabecalho[6:].strip()).decode("utf-8").partition(":")
        except Exception:
            return False
        # compare_digest nos dois campos: comparação de tempo constante.
        ok_user = secrets.compare_digest(usuario, type(self).auth_user or "")
        ok_senha = secrets.compare_digest(senha, type(self).auth_password or "")
        return ok_user and ok_senha

    def _pedir_credenciais(self):
        corpo = b"Acesso restrito ao dashboard do pipeline.\n"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="CNPJ Pipeline", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        try:
            self.wfile.write(corpo)
        except BrokenPipeError:
            pass

    # -- rotas --------------------------------------------------------------

    def do_GET(self):  # noqa: N802 (assinatura da stdlib)
        if not self._autenticado():
            self._pedir_credenciais()
            return

        rota = self.path.split("?")[0].rstrip("/") or "/"
        if rota in ("/", "/index.html"):
            corpo = (PAGE
                     .replace("__ALPINE__", ALPINE_CDN)
                     .replace("__REFRESH__", str(DASHBOARD_REFRESH_SECONDS)))
            self._responder(200, "text/html; charset=utf-8", corpo.encode("utf-8"))
        elif rota in ("/state.json", "/state"):
            self._responder_estado()
        else:
            self._responder(404, "text/plain; charset=utf-8", b"nao encontrado")

    def _responder_estado(self):
        caminho = type(self).state_path
        if not caminho or not Path(caminho).exists():
            self._responder(404, "application/json", b'{"error":"estado ainda nao criado"}')
            return
        try:
            dados = Path(caminho).read_bytes()
            json.loads(dados)   # não serve um JSON truncado por escrita concorrente
        except (OSError, json.JSONDecodeError):
            self._responder(503, "application/json", b'{"error":"estado indisponivel no momento"}')
            return
        self._responder(200, "application/json; charset=utf-8", dados)

    def _responder(self, status: int, content_type: str, corpo: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        # X-Robots-Tag cobre também o /state.json, onde não cabe <meta>.
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(corpo)
        except BrokenPipeError:
            pass   # aba fechada no meio da resposta

    def log_message(self, *args):
        """Silencia o log de acesso — poluiria o log da carga."""
        return


def gerar_senha() -> str:
    """Senha curta e legível para uma sessão temporária de dashboard."""
    return secrets.token_urlsafe(9)


def start_dashboard(
        state_path: Path,
        port: int,
        host: str = "127.0.0.1",
        password: Optional[str] = None,
        user: str = DASHBOARD_USER,
        auth: bool = True,
):
    """Sobe o dashboard numa thread daemon. Retorna o servidor, ou None se falhar.

    Nunca levanta exceção: uma porta ocupada não pode impedir a carga de rodar.

    :param password: senha do Basic Auth. Se `auth` estiver ligado e nenhuma
                     senha for informada, uma é gerada e mostrada no log.
    :param auth: `False` desliga a autenticação (`--no-auth`).
    """
    senha = None
    if auth:
        senha = password or gerar_senha()

    handler = type("Handler", (_Handler,), {
        "state_path": Path(state_path),
        "auth_user": user,
        "auth_password": senha,
    })

    try:
        servidor = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print_log(
            f"DASHBOARD NÃO PÔDE SUBIR EM {host}:{port} ({exc}) — pipeline segue normalmente",
            level="warning"
        )
        return None

    thread = threading.Thread(target=servidor.serve_forever, daemon=True,
                              name="dashboard")
    thread.start()

    visivel = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    porta_real = servidor.server_address[1]   # com port=0 o SO escolhe uma livre
    print_log(f"DASHBOARD DISPONÍVEL EM http://{visivel}:{porta_real}", level="web")

    if senha:
        print_log(f"  -> usuário: {user}   senha: {senha}", level="search")
        if not password:
            print_log("  -> senha gerada automaticamente; defina --dashboard-password "
                      "para escolher a sua", level="docs")
    else:
        print_log("  -> SEM AUTENTICAÇÃO (--no-auth): qualquer um com acesso à porta "
                  "vê o andamento", level="warning")

    if host == "127.0.0.1":
        print_log("  -> ouvindo apenas em 127.0.0.1; use --host 0.0.0.0 para acessar "
                  "de fora do container", level="docs")
    return servidor
