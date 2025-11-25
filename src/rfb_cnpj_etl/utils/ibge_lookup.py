"""
Utilitários para mapeamento SIAFI → IBGE e cache dos arquivos CSV de localidade.
"""

import csv
from pathlib import Path
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple
from .logger import print_log
from ..config import IBGE_REGIOES_CSV, IBGE_ESTADOS_CSV, IBGE_CIDADES_CSV


class IBGELookup:
    """
    Carrega CSVs de regiões, estados e cidades e oferece lookup O(1)
    para os códigos IBGE.
    """

    def __init__(
            self,
            regioes_csv: Path = IBGE_REGIOES_CSV,
            estados_csv: Path = IBGE_ESTADOS_CSV,
            cidades_csv: Path = IBGE_CIDADES_CSV
    ):
        self.regioes_csv = Path(regioes_csv)
        self.estados_csv = Path(estados_csv)
        self.cidades_csv = Path(cidades_csv)

        self._lock = Lock()
        self._loaded = False
        self._available = False
        self._missing_log_emitted = False

        self.regioes: Dict[str, Dict] = {}
        self.estados_por_sigla: Dict[str, Dict] = {}
        self.estados_por_codigo: Dict[str, Dict] = {}
        self.cidades_por_siafi: Dict[str, Dict] = {}
        self._misses_notificados = 0

    # ------------------------------------------------------------------
    # Leitura dos CSVs
    # ------------------------------------------------------------------
    def _read_csv(self, csv_path: Path) -> Iterable[Dict[str, str]]:
        linhas = csv_path.read_text(encoding="utf-8").splitlines()
        if not linhas:
            return []

        sample_text = "\n".join(linhas[:5])
        delimiter = ","
        try:
            sniffed = csv.Sniffer().sniff(sample_text, delimiters=";,")
            delimiter = sniffed.delimiter
        except csv.Error:
            pass

        with csv_path.open("r", encoding="utf-8") as handler:
            reader = csv.DictReader(handler, delimiter=delimiter)
            for row in reader:
                yield {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}

    def _normalize_codigo(self, valor: Optional[str]) -> Optional[str]:
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

    def _load_regioes(self):
        for row in self._read_csv(self.regioes_csv):
            codigo = self._normalize_codigo(row.get("cod_regiao_ibge") or row.get("ibge_code"))
            if not codigo:
                continue
            self.regioes[codigo] = {
                "cod_regiao_ibge": codigo,
                "sigla_regiao": row.get("sigla_regiao") or row.get("abbreviation"),
                "nome_regiao": row.get("nome_regiao") or row.get("name"),
                "slug_regiao": row.get("slug_regiao") or row.get("slug"),
            }

    def _load_estados(self):
        for row in self._read_csv(self.estados_csv):
            codigo = self._normalize_codigo(row.get("cod_estado_ibge") or row.get("ibge_code") or row.get("state_code"))
            sigla = (row.get("sigla_estado") or row.get("sigla_uf") or row.get("abbreviation") or "").upper().strip()
            if not codigo or not sigla:
                continue
            regiao = self._normalize_codigo(row.get("cod_regiao_ibge") or row.get("region_id"))

            estado = {
                "cod_estado_ibge": codigo,
                "sigla_uf": sigla,
                "nome_estado": row.get("nome_estado") or row.get("name"),
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
                "cod_regiao_ibge": regiao,
                "slug_estado": row.get("slug_estado") or row.get("slug"),
            }
            self.estados_por_sigla[sigla] = estado
            self.estados_por_codigo[codigo] = estado

    def _load_cidades(self):
        for row in self._read_csv(self.cidades_csv):
            siafi = self._normalize_codigo(row.get("cod_municipio") or row.get("siafi_id"))
            codigo = self._normalize_codigo(row.get("cod_cidade_ibge") or row.get("ibge_code"))
            estado_codigo = self._normalize_codigo(row.get("cod_estado_ibge") or row.get("state_code"))
            if not siafi or not codigo:
                continue

            cidade = {
                "cod_cidade_ibge": codigo,
                "nome_cidade": row.get("nome_cidade") or row.get("name"),
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
                "capital": row.get("capital") or row.get("is_capital"),
                "cod_estado_ibge": estado_codigo,
                "cod_municipio": siafi,
                "ddd": row.get("ddd"),
                "fuso_horario": row.get("fuso_horario") or row.get("timezone"),
                "slug_cidade": row.get("slug_cidade") or row.get("slug"),
            }

            self.cidades_por_siafi[siafi] = cidade
            # Também indexa sem zeros à esquerda para robustez
            self.cidades_por_siafi[siafi.lstrip("0")] = cidade

    def _load_all(self):
        paths = [self.regioes_csv, self.estados_csv, self.cidades_csv]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            if not self._missing_log_emitted:
                print_log(
                    f"ARQUIVOS CSV IBGE NÃO ENCONTRADOS: {', '.join(missing)}. "
                    f"Informe o caminho em IBGE_CSV_DIR ou IBGE_*_CSV.",
                    level="warning"
                )
                self._missing_log_emitted = True
            return

        self._load_regioes()
        self._load_estados()
        self._load_cidades()
        self._available = True

    def ensure_loaded(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_all()
            self._loaded = True

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def _lookup_estado(self, codigo_estado: Optional[str], sigla_uf: Optional[str]) -> Optional[Dict]:
        if codigo_estado and codigo_estado in self.estados_por_codigo:
            return self.estados_por_codigo[codigo_estado]
        if sigla_uf:
            uf = sigla_uf.strip().upper()
            return self.estados_por_sigla.get(uf)
        return None

    def lookup_codigos(self, cod_municipio: Optional[str], sigla_uf: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Recebe um código SIAFI/CIAF e retorna (cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge).
        """
        self.ensure_loaded()
        if not self._available:
            return None, None, None

        siafi = self._normalize_codigo(cod_municipio)
        uf = sigla_uf.strip().upper() if sigla_uf else None

        cidade = None
        if siafi:
            cidade = (
                self.cidades_por_siafi.get(siafi)
                or self.cidades_por_siafi.get(siafi.zfill(4))
                or self.cidades_por_siafi.get(siafi.zfill(7))
            )

        estado = None
        if cidade:
            estado = self._lookup_estado(cidade.get("cod_estado_ibge"), uf)
        elif uf:
            estado = self._lookup_estado(None, uf)

        cod_cidade_ibge = cidade["cod_cidade_ibge"] if cidade else None
        cod_estado_ibge = estado["cod_estado_ibge"] if estado else None

        cod_regiao_ibge = None
        if estado and estado.get("cod_regiao_ibge"):
            cod_regiao_ibge = estado["cod_regiao_ibge"]
        elif uf and not self._misses_notificados:
            print_log(f"REGIÃO IBGE NÃO ENCONTRADA PARA UF {uf}", level="warning")
            self._misses_notificados += 1

        if not cidade and siafi and self._misses_notificados < 5:
            print_log(f"CÓDIGO SIAFI SEM MAPEAMENTO IBGE: {siafi}", level="warning")
            self._misses_notificados += 1

        return cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge

    # ------------------------------------------------------------------
    # Dados para carga de tabelas de referência
    # ------------------------------------------------------------------
    def get_reference_rows(self) -> Dict[str, List[List]]:
        """
        Retorna linhas já normalizadas para inserir em regiao/estado/cidade.
        """
        self.ensure_loaded()
        if not self._available:
            return {}

        regioes_rows = [
            [
                reg["cod_regiao_ibge"],
                reg.get("sigla_regiao"),
                reg.get("nome_regiao"),
                reg.get("slug_regiao")
            ]
            for reg in self.regioes.values()
        ]

        estados_rows = [
            [
                est["cod_estado_ibge"],
                est.get("sigla_uf"),
                est.get("nome_estado"),
                est.get("latitude"),
                est.get("longitude"),
                est.get("cod_regiao_ibge"),
                est.get("slug_estado")
            ]
            for est in self.estados_por_codigo.values()
        ]

        cidades_rows = []
        vistos = set()
        for cid in self.cidades_por_siafi.values():
            chave = cid.get("cod_cidade_ibge") or cid.get("cod_municipio")
            if chave in vistos:
                continue
            vistos.add(chave)
            cidades_rows.append([
                cid.get("cod_cidade_ibge"),
                cid.get("nome_cidade"),
                cid.get("latitude"),
                cid.get("longitude"),
                cid.get("capital"),
                cid.get("cod_estado_ibge"),
                cid.get("cod_municipio"),
                cid.get("ddd"),
                cid.get("fuso_horario"),
                cid.get("slug_cidade")
            ])

        return {
            "regiao": regioes_rows,
            "estado": estados_rows,
            "cidade": cidades_rows
        }

    def append_ibge_to_estabelecimentos(self, rows: List[List], columns: List[str]) -> List[List]:
        """
        Enriquecimento dos estabelecimentos com códigos IBGE.
        """
        base_len = len(columns) - 3  # colunas originais sem os novos campos
        try:
            idx_municipio = columns.index("cod_municipio")
        except ValueError:
            idx_municipio = None
        try:
            idx_uf = columns.index("uf")
        except ValueError:
            idx_uf = None

        enriched = []
        for row in rows:
            nova_linha = list(row)
            cod_municipio = nova_linha[idx_municipio] if idx_municipio is not None and idx_municipio < len(nova_linha) else None
            uf = nova_linha[idx_uf] if idx_uf is not None and idx_uf < len(nova_linha) else None

            cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge = self.lookup_codigos(cod_municipio, uf)

            # garante alinhamento antes de anexar os novos campos
            while len(nova_linha) < base_len:
                nova_linha.append(None)
            nova_linha.extend([cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge])

            enriched.append(nova_linha)
        return enriched
