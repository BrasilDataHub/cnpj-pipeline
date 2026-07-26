"""
Utilities for SIAFI → IBGE mapping and caching of the locality CSV files.
"""

import csv
from pathlib import Path
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple
from .logger import print_log
from ..config import IBGE_REGIOES_CSV, IBGE_ESTADOS_CSV, IBGE_CIDADES_CSV, BRAZIL_COUNTRY_CODES


class IBGELookup:
    """
    Loads the region, state and city CSVs and provides O(1) lookup
    for the IBGE codes.
    """

    def __init__(
            self,
            regions_csv: Path = IBGE_REGIOES_CSV,
            states_csv: Path = IBGE_ESTADOS_CSV,
            cities_csv: Path = IBGE_CIDADES_CSV
    ):
        self.regions_csv = Path(regions_csv)
        self.states_csv = Path(states_csv)
        self.cities_csv = Path(cities_csv)

        self._lock = Lock()
        self._loaded = False
        self._available = False
        self._missing_log_emitted = False

        self.regions: Dict[str, Dict] = {}
        self.states_by_abbrev: Dict[str, Dict] = {}
        self.states_by_code: Dict[str, Dict] = {}
        self.cities_by_siafi: Dict[str, Dict] = {}
        self._misses_reported = 0

    # ------------------------------------------------------------------
    # CSV reading
    # ------------------------------------------------------------------
    def _read_csv(self, csv_path: Path) -> Iterable[Dict[str, str]]:
        # utf-8-sig strips the BOM present in some of the distributed CSVs
        lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
        if not lines:
            return []

        sample_text = "\n".join(lines[:5])
        delimiter = ","
        try:
            sniffed = csv.Sniffer().sniff(sample_text, delimiters=";,")
            delimiter = sniffed.delimiter
        except csv.Error:
            pass

        with csv_path.open("r", encoding="utf-8-sig") as handler:
            reader = csv.DictReader(handler, delimiter=delimiter)
            for row in reader:
                sanitized = {}
                for k, v in row.items():
                    key = k.lstrip("\ufeff").strip() if isinstance(k, str) else k
                    value = v.strip() if isinstance(v, str) else v
                    sanitized[key] = value
                yield sanitized

    def _normalize_code(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _load_regions(self):
        for row in self._read_csv(self.regions_csv):
            code = self._normalize_code(row.get("cod_regiao_ibge") or row.get("ibge_code"))
            if not code:
                continue
            self.regions[code] = {
                "cod_regiao_ibge": code,
                "sigla_regiao": row.get("sigla_regiao") or row.get("abbreviation"),
                "nome_regiao": row.get("nome_regiao") or row.get("name"),
            }

    def _load_states(self):
        for row in self._read_csv(self.states_csv):
            code = self._normalize_code(row.get("cod_estado_ibge") or row.get("ibge_code") or row.get("state_code"))
            abbrev = (row.get("sigla_estado") or row.get("sigla_uf") or row.get("abbreviation") or "").upper().strip()
            if not code or not abbrev:
                continue
            region = self._normalize_code(row.get("cod_regiao_ibge") or row.get("region_id"))

            state = {
                "cod_estado_ibge": code,
                "sigla_uf": abbrev,
                "nome_estado": row.get("nome_estado") or row.get("name"),
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
                "cod_regiao_ibge": region,
            }
            self.states_by_abbrev[abbrev] = state
            self.states_by_code[code] = state

    def _load_cities(self):
        for row in self._read_csv(self.cities_csv):
            siafi = self._normalize_code(row.get("cod_municipio") or row.get("siafi_id"))
            code = self._normalize_code(row.get("cod_cidade_ibge") or row.get("ibge_code"))
            state_code = self._normalize_code(row.get("cod_estado_ibge") or row.get("state_code"))
            if not siafi or not code:
                continue

            city = {
                "cod_cidade_ibge": code,
                "nome_cidade": row.get("nome_cidade") or row.get("name"),
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
                "capital": row.get("capital") or row.get("is_capital"),
                "cod_estado_ibge": state_code,
                "cod_municipio": siafi,
                "ddd": row.get("ddd"),
                "fuso_horario": row.get("fuso_horario") or row.get("timezone"),
            }

            self.cities_by_siafi[siafi] = city
            # Also index without leading zeros for robustness
            self.cities_by_siafi[siafi.lstrip("0")] = city

    def _load_all(self):
        paths = [self.regions_csv, self.states_csv, self.cities_csv]
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

        self._load_regions()
        self._load_states()
        self._load_cities()
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
    def _lookup_state(self, state_code: Optional[str], sigla_uf: Optional[str]) -> Optional[Dict]:
        if state_code and state_code in self.states_by_code:
            return self.states_by_code[state_code]
        if sigla_uf:
            uf = sigla_uf.strip().upper()
            return self.states_by_abbrev.get(uf)
        return None

    def lookup_ibge_codes(self, cod_municipio: Optional[str], sigla_uf: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Takes a SIAFI/CIAF code and returns (cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge).
        """
        self.ensure_loaded()
        if not self._available:
            return None, None, None

        siafi = self._normalize_code(cod_municipio)
        uf = sigla_uf.strip().upper() if sigla_uf else None

        city = None
        if siafi:
            city = (
                self.cities_by_siafi.get(siafi)
                or self.cities_by_siafi.get(siafi.zfill(4))
                or self.cities_by_siafi.get(siafi.zfill(7))
            )

        state = None
        if city:
            state = self._lookup_state(city.get("cod_estado_ibge"), uf)
        elif uf:
            state = self._lookup_state(None, uf)

        cod_cidade_ibge = city["cod_cidade_ibge"] if city else None
        cod_estado_ibge = state["cod_estado_ibge"] if state else None

        cod_regiao_ibge = None
        if state and state.get("cod_regiao_ibge"):
            cod_regiao_ibge = state["cod_regiao_ibge"]
        elif uf and not self._misses_reported:
            print_log(f"REGIÃO IBGE NÃO ENCONTRADA PARA UF {uf}", level="warning")
            self._misses_reported += 1

        if not city and siafi and self._misses_reported < 5:
            print_log(f"CÓDIGO SIAFI SEM MAPEAMENTO IBGE: {siafi}", level="warning")
            self._misses_reported += 1

        return cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge

    # ------------------------------------------------------------------
    # Data for loading the reference tables
    # ------------------------------------------------------------------
    def get_reference_rows(self) -> Dict[str, List[List]]:
        """
        Returns already normalized rows ready to be inserted into regiao/estado/cidade.
        """
        self.ensure_loaded()
        if not self._available:
            return {}

        region_rows = [
            [
                reg["cod_regiao_ibge"],
                reg.get("sigla_regiao"),
                reg.get("nome_regiao"),
            ]
            for reg in self.regions.values()
        ]

        state_rows = [
            [
                est["cod_estado_ibge"],
                est.get("sigla_uf"),
                est.get("nome_estado"),
                est.get("latitude"),
                est.get("longitude"),
                est.get("cod_regiao_ibge"),
            ]
            for est in self.states_by_code.values()
        ]

        city_rows = []
        seen = set()
        for cid in self.cities_by_siafi.values():
            key = cid.get("cod_cidade_ibge") or cid.get("cod_municipio")
            if key in seen:
                continue
            seen.add(key)
            city_rows.append([
                cid.get("cod_cidade_ibge"),
                cid.get("nome_cidade"),
                cid.get("latitude"),
                cid.get("longitude"),
                cid.get("capital"),
                cid.get("cod_estado_ibge"),
                cid.get("cod_municipio"),
                cid.get("ddd"),
                cid.get("fuso_horario"),
            ])

        return {
            "ibge_regiao": region_rows,
            "ibge_estado": state_rows,
            "ibge_cidade": city_rows
        }

    def append_ibge_codes(self, rows: List[List], columns: List[str]) -> List[List]:
        """
        Enrichment of the establishment rows with IBGE codes.
        """
        base_len = len(columns) - 3  # original columns without the new fields
        try:
            idx_municipio = columns.index("cod_municipio")
        except ValueError:
            idx_municipio = None
        try:
            idx_uf = columns.index("uf")
        except ValueError:
            idx_uf = None
        try:
            idx_pais = columns.index("cod_pais")
        except ValueError:
            idx_pais = None

        enriched = []
        for row in rows:
            new_row = list(row)
            cod_municipio = new_row[idx_municipio] if idx_municipio is not None and idx_municipio < len(new_row) else None
            uf = new_row[idx_uf] if idx_uf is not None and idx_uf < len(new_row) else None
            cod_pais = new_row[idx_pais] if idx_pais is not None and idx_pais < len(new_row) else None

            if cod_pais and str(cod_pais).strip() not in BRAZIL_COUNTRY_CODES:
                cod_regiao_ibge = cod_estado_ibge = cod_cidade_ibge = None
            else:
                cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge = self.lookup_ibge_codes(cod_municipio, uf)

            # ensures alignment before appending the new fields
            while len(new_row) < base_len:
                new_row.append(None)
            new_row.extend([cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge])

            enriched.append(new_row)
        return enriched
