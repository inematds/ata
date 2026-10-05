"""Servidor MCP do Ata: `ata mcp` (stdio) ou `ata mcp --http [--port]` (só 127.0.0.1).

Ferramentas ``ata_*`` (≥ 18) com resultado JSON estruturado, paginação ``limit``/``cursor`` e recursos
``ata://<id>`` (nota) e ``ata://<id>/transcript``. Toda a lógica vive em :class:`Tools` (testável sem SDK);
o SDK ``mcp`` é usado só para o transporte (FastMCP no 1.x, MCPServer no 2.x).

A camada de dados é ``ata.dashboard.api``, que chama gravador (A), pipeline (B) e conhecimento (D) por import
preguiçoso. Erros viram ``{"ok": false, "error": "..."}`` em pt-BR; nunca texto de reunião em log.
"""

from __future__ import annotations

import argparse
import functools
import sys
from typing import Any, Callable

from .config import Config, load_config
from .dashboard import api

SERVER_NAME = "ata"
INSTRUCTIONS = (
    "Ata: notas de reunião local-first (meeting notes, transcripts, action items, decisions). "
    "Use ata_list/ata_search para achar reuniões, ata_read para ler resumo/transcrição (paginado), "
    "ata_actions/ata_decisions entre reuniões, ata_prep antes de uma reunião, ata_record_* para gravar e "
    "ata_process + ata_job para processar. Cite trechos como [título, mm:ss]."
)


def _wrap(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def inner(*a: Any, **kw: Any) -> dict[str, Any]:
        try:
            out = fn(*a, **kw)
            return out if isinstance(out, dict) else {"result": out}
        except api.ApiError as exc:
            return {"ok": False, "error": str(exc), "status": exc.status}
        except Exception as exc:  # noqa: BLE001 - erro sem texto de reunião
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return inner


class Tools:
    """Implementação das ferramentas; cada método devolve um dict JSON."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.jobs = api.Jobs()

    def status(self) -> dict[str, Any]:
        st = api.recorder_status(self.config)
        live = api.current_live_bundle(self.config)
        return {"recorder": st, "live_bundle": live.name if live else None,
                "jobs": [j for j in self.jobs.list() if j["status"] in ("queued", "running")],
                "recordings": str(self.config.recordings),
                "engines_forced_fake": __import__("ata.engines.registry", fromlist=["x"]).forced_fake()}

    def list(self, **kw: Any) -> dict[str, Any]:
        return api.list_meetings(self.config, **kw)

    def read(self, meeting_id: str, **kw: Any) -> dict[str, Any]:
        return api.meeting_detail(self.config, meeting_id, **kw)

    def search(self, query: str, **kw: Any) -> dict[str, Any]:
        return api.search(self.config, query, **kw)

    def ask(self, question: str, meeting_id: str | None = None) -> dict[str, Any]:
        return api.ask(self.config, question, meeting=meeting_id)

    def actions(self, **kw: Any) -> dict[str, Any]:
        return api.collect_items(self.config, "actions", **kw)

    def decisions(self, **kw: Any) -> dict[str, Any]:
        return api.collect_items(self.config, "decisions", **kw)

    def prep(self, title: str, days: int = 90) -> dict[str, Any]:
        return api.prep(self.config, title, days=days)

    def person(self, name: str, **kw: Any) -> dict[str, Any]:
        return api.person_timeline(self.config, name, **kw)

    def record_start(self, **kw: Any) -> dict[str, Any]:
        return api.record_start(self.config, **kw)

    def record_stop(self, process: bool = True) -> dict[str, Any]:
        return api.record_stop(self.config, process=process)

    def record_toggle(self, title: str | None = None) -> dict[str, Any]:
        return api.record_toggle(self.config, title=title)

    def process(self, meeting_id: str, **kw: Any) -> dict[str, Any]:
        return self.jobs.submit(self.config, meeting_id, **kw)

    def job(self, job_id: str | None = None) -> dict[str, Any]:
        if not job_id:
            return {"jobs": self.jobs.list()}
        return self.jobs.get(job_id)

    def rename_speakers(self, meeting_id: str, names: dict[str, str]) -> dict[str, Any]:
        return api.rename_speakers(self.config, meeting_id, names)

    def export(self, meeting_id: str, fmt: str = "md", out: str | None = None) -> dict[str, Any]:
        return api.export(self.config, meeting_id, fmt, out)

    def reindex(self) -> dict[str, Any]:
        return api.reindex(self.config)

    def doctor(self) -> dict[str, Any]:
        return api.doctor(self.config)


# ---- registro no SDK ---------------------------------------------------------------------------------------

def _server_class() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x
        return FastMCP
    except ImportError:
        pass
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2.x
        return MCPServer
    except ImportError:
        return None


TOOL_NAMES = ("ata_status", "ata_list", "ata_read", "ata_search", "ata_ask", "ata_actions", "ata_decisions",
              "ata_prep", "ata_person", "ata_record_start", "ata_record_stop", "ata_record_toggle",
              "ata_process", "ata_job", "ata_rename_speakers", "ata_export", "ata_reindex", "ata_doctor")


def build_server(config: Config, tools: Tools | None = None) -> Any:
    cls = _server_class()
    if cls is None:
        raise ImportError("pacote 'mcp' ausente: instale com `uv pip install 'ata[mcp]'`")
    T = tools or Tools(config)
    srv = cls(SERVER_NAME, instructions=INSTRUCTIONS)

    def tool(name: str, desc: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            srv.add_tool(_wrap(fn), name=name, description=desc)
            return fn
        return deco

    @tool("ata_status", "Estado do Ata (status): gravador (idle/recording), bundle atual, ao vivo (live), "
                        "trabalhos em andamento (jobs).")
    def ata_status() -> dict[str, Any]:
        return T.status()

    @tool("ata_list", "Lista reuniões (list meetings), mais recentes primeiro. Filtros: date_from/date_to "
                      "(AAAA-MM-DD), participant, language, query (título). Paginação: limit + cursor "
                      "(use next_cursor).")
    def ata_list(date_from: str | None = None, date_to: str | None = None, participant: str | None = None,
                 language: str | None = None, query: str | None = None, limit: int = 20,
                 cursor: str | None = None) -> dict[str, Any]:
        return T.list(date_from=date_from, date_to=date_to, participant=participant, language=language,
                      query=query, limit=limit, cursor=cursor)

    @tool("ata_read", "Lê uma reunião (read meeting): parts = meta, summary, decisions, actions, questions, "
                      "transcript, note, my_notes. Transcrição paginada (limit/cursor) e recortável por "
                      "from_s/to_s (segundos). meeting_id = nome da pasta ou 'latest'.")
    def ata_read(meeting_id: str, parts: list[str] | None = None, from_s: float | None = None,
                 to_s: float | None = None, limit: int = 200, cursor: str | None = None) -> dict[str, Any]:
        return T.read(meeting_id, parts=parts, from_s=from_s, to_s=to_s, limit=limit, cursor=cursor)

    @tool("ata_search", "Busca nas transcrições (search transcripts): mode hybrid|lexical|semantic. Devolve "
                        "trechos com meeting, title, start (s), t (mm:ss), speaker, score. Paginação limit/cursor.")
    def ata_search(query: str, mode: str = "hybrid", meeting_id: str | None = None,
                   speaker: str | None = None, date_from: str | None = None, limit: int = 10,
                   cursor: str | None = None) -> dict[str, Any]:
        filters = {k: v for k, v in {"meeting": meeting_id, "speaker": speaker, "date_from": date_from}.items()
                   if v}
        return T.search(query, mode=mode, limit=limit, cursor=cursor, filters=filters)

    @tool("ata_ask", "Pergunta sobre as reuniões (ask meetings): devolve evidências com citação "
                     "[título, mm:ss] e, se houver resumidor, uma resposta. meeting_id opcional restringe a uma.")
    def ata_ask(question: str, meeting_id: str | None = None) -> dict[str, Any]:
        return T.ask(question, meeting_id)

    @tool("ata_actions", "Ações (action items) entre reuniões, de summary.json: texto, owner (responsável), "
                         "due (prazo), evidência. Filtros owner, since (AAAA-MM-DD), query. limit/cursor.")
    def ata_actions(owner: str | None = None, since: str | None = None, query: str | None = None,
                    limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        return T.actions(owner=owner, since=since, query=query, limit=limit, cursor=cursor)

    @tool("ata_decisions", "Decisões (decisions) entre reuniões, com evidência [mm:ss]. Filtros since, query. "
                           "limit/cursor.")
    def ata_decisions(since: str | None = None, query: str | None = None, limit: int = 50,
                      cursor: str | None = None) -> dict[str, Any]:
        return T.decisions(since=since, query=query, limit=limit, cursor=cursor)

    @tool("ata_prep", "Cola de preparação (meeting prep / cheatsheet) para uma próxima reunião: ações em "
                      "aberto, decisões anteriores, perguntas e roteiro com itens (qN). days = janela.")
    def ata_prep(title: str, days: int = 90) -> dict[str, Any]:
        return T.prep(title, days)

    @tool("ata_person", "Linha do tempo de uma pessoa (person timeline): reuniões em que falou, tempo de fala, "
                        "ações sob sua responsabilidade.")
    def ata_person(name: str, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        return T.person(name, limit=limit, cursor=cursor)

    @tool("ata_record_start", "Começa a gravar (start recording) as duas faixas (far = os outros, mic = você).")
    def ata_record_start(title: str | None = None, speakers: int | None = None,
                         language: str | None = None) -> dict[str, Any]:
        return T.record_start(title=title, speakers=speakers, language=language)

    @tool("ata_record_stop", "Para a gravação (stop recording); process=true processa e gera a nota.")
    def ata_record_stop(process: bool = True) -> dict[str, Any]:
        return T.record_stop(process)

    @tool("ata_record_toggle", "Alterna gravação (toggle recording): começa se parado, para se gravando.")
    def ata_record_toggle(title: str | None = None) -> dict[str, Any]:
        return T.record_toggle(title)

    @tool("ata_process", "Processa uma gravação (process / transcribe) em segundo plano: devolve job_id; "
                         "acompanhe com ata_job.")
    def ata_process(meeting_id: str, language: str | None = None, speakers: int | None = None,
                    summarize: bool = True) -> dict[str, Any]:
        return T.process(meeting_id, language=language, speakers=speakers, summarize=summarize)

    @tool("ata_job", "Progresso de um trabalho (job progress): status queued|running|done|error, progress "
                     "0..1, note (caminho da nota). Sem job_id lista todos.")
    def ata_job(job_id: str | None = None) -> dict[str, Any]:
        return T.job(job_id)

    @tool("ata_rename_speakers", "Renomeia falantes (rename speakers) de uma reunião, ex. names = "
                                 "{\"Pessoa 2\": \"Ana\"}; re-renderiza a nota.")
    def ata_rename_speakers(meeting_id: str, names: dict[str, str]) -> dict[str, Any]:
        return T.rename_speakers(meeting_id, names)

    @tool("ata_export", "Exporta a transcrição (export): fmt = srt|vtt|txt|json|md|csv; out = caminho opcional.")
    def ata_export(meeting_id: str, fmt: str = "md", out: str | None = None) -> dict[str, Any]:
        return T.export(meeting_id, fmt, out)

    @tool("ata_reindex", "Reconstrói o índice de busca (reindex) a partir de todas as reuniões.")
    def ata_reindex() -> dict[str, Any]:
        return T.reindex()

    @tool("ata_doctor", "Diagnóstico (doctor): plataforma, pastas, motores, CLIs e dependências.")
    def ata_doctor() -> dict[str, Any]:
        return T.doctor()

    @srv.resource("ata://{meeting_id}", name="nota", description="Nota Markdown da reunião (meeting note)",
                  mime_type="text/markdown")
    def note_resource(meeting_id: str) -> str:
        try:
            return api.note_text(config, meeting_id)
        except api.ApiError as exc:
            return f"erro: {exc}"

    @srv.resource("ata://{meeting_id}/transcript", name="transcricao",
                  description="Transcrição [mm:ss] Falante: texto (meeting transcript)", mime_type="text/plain")
    def transcript_resource(meeting_id: str) -> str:
        try:
            return api.transcript_text(config, meeting_id)
        except api.ApiError as exc:
            return f"erro: {exc}"

    @srv.prompt(name="acoes-abertas", description="Lista as ações em aberto agrupadas por responsável")
    def acoes_abertas() -> str:
        return ("Use a ferramenta ata_actions (limit 200) e agrupe as ações por owner, com prazo e citação "
                "[título, mm:ss]. Responda em pt-BR.")

    @srv.prompt(name="prep", description="Prepara a próxima reunião sobre um assunto")
    def prep_prompt(assunto: str) -> str:
        return (f"Use ata_prep com title={assunto!r} e monte uma cola curta: ações em aberto, decisões já "
                "tomadas, perguntas pendentes e o roteiro com os itens (qN).")

    return srv


# ---- comando -----------------------------------------------------------------------------------------------

def run(config: Config, *, http: bool = False, port: int | None = None) -> int:
    try:
        srv = build_server(config)
    except ImportError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 4
    if http:
        p = int(port or 47531)
        print(f"ata mcp: http://127.0.0.1:{p}/mcp", file=sys.stderr)
        srv.run(transport="streamable-http", host="127.0.0.1", port=p)
    else:
        srv.run()
    return 0


def _cmd(args: argparse.Namespace, config: Config) -> int:
    return run(config, http=args.http, port=args.port)


def add_parser(sub: Any) -> None:
    p = sub.add_parser("mcp", help="servidor MCP (stdio; --http em 127.0.0.1) para agentes",
                       description="Servidor MCP do Ata com ferramentas ata_* para Claude Code, Codex e afins.")
    p.add_argument("--http", action="store_true", help="HTTP streamable em 127.0.0.1 em vez de stdio")
    p.add_argument("--port", type=int, default=None, help="porta do --http (padrão 47531)")
    p.set_defaults(func=_cmd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ata.mcp_server")
    ap.add_argument("--config")
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--port", type=int)
    a = ap.parse_args(argv)
    return run(load_config(a.config), http=a.http, port=a.port)


if __name__ == "__main__":
    raise SystemExit(main())
