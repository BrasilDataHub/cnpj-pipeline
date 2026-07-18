# cnpj_data/cnpj_public_data.py

"""
Módulo para acessar os dados de CNPJ disponíveis no servidor WebDAV da Receita Federal (Nextcloud).

A Receita Federal migrou seus arquivos para um servidor WebDAV (Nextcloud), tornando
necessário utilizar PROPFIND para listar diretórios e GET para download de arquivos.

A URL base é configurável via variável de ambiente RFB_WEBDAV_URL.
"""

import os
import re
import requests
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from typing import Dict, List, Tuple, Optional
from ..config import CNPJ_WEBDAV_BASE_URL

# Namespace DAV para parsing XML
_DAV_NS = {'d': 'DAV:'}

# Body mínimo para PROPFIND — solicita nome, tamanho e tipo de recurso
_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<d:propfind xmlns:d="DAV:">'
    '<d:prop>'
    '<d:displayname/>'
    '<d:getcontentlength/>'
    '<d:resourcetype/>'
    '</d:prop>'
    '</d:propfind>'
)


class CNPJDataScraper:
    """
    Classe para acessar os dados de CNPJ disponíveis no servidor WebDAV da Receita Federal.

    Utiliza PROPFIND para listar diretórios e arquivos. O download é feito
    via GET padrão HTTP (compatível com WebDAV).

    :params:
        webdav_base_url: URL base WebDAV para o diretório CNPJ.
        _session: sessão HTTP persistente.
    """

    def __init__(self):
        self.webdav_base_url = CNPJ_WEBDAV_BASE_URL.rstrip('/') + '/'
        self._session = requests.Session()

    @staticmethod
    def _is_valid_period(month_year: str) -> bool:
        """
        Verifica se uma string está no formato MM/AAAA.

        :param month_year: string no formato MM/AAAA
        :return: bool
        """
        match = re.match(r'^(\d{2})/(\d{4})$', month_year)
        return bool(match)

    @staticmethod
    def _parse_month(month_year: str) -> Tuple[int, int]:
        """
        Converte uma string no formato AAAA-MM em um par (ano, mês).

        :param month_year: string no formato AAAA-MM
        :return: par (ano, mês)
        """
        year_month = month_year.rstrip('/')
        try:
            year, month = year_month.split('-')
            return int(year), int(month)
        except ValueError:
            return 0, 0

    def _propfind(self, url: str, depth: int = 1) -> List[Dict]:
        """
        Executa uma requisição WebDAV PROPFIND e retorna as entradas parseadas.

        :param url: URL do recurso WebDAV
        :param depth: profundidade da listagem (0=self, 1=filhos imediatos)
        :return: Lista de dicionários com informações de cada entrada
        """
        headers = {
            'Depth': str(depth),
            'Content-Type': 'application/xml; charset=utf-8',
        }

        try:
            resp = self._session.request(
                'PROPFIND', url,
                headers=headers,
                data=_PROPFIND_BODY,
                timeout=60
            )
            resp.raise_for_status()
        except requests.ConnectionError:
            raise ValueError(
                f"NÃO FOI POSSÍVEL CONECTAR AO SERVIDOR WEBDAV: {url}\n"
                f"Verifique sua conexão de internet e se o servidor está acessível."
            )
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 'desconhecido'
            if status == 405:
                raise ValueError(
                    f"SERVIDOR NÃO SUPORTA WEBDAV (PROPFIND): {url}\n"
                    f"O endpoint pode ter mudado. Atualize RFB_WEBDAV_URL."
                )
            elif status in (401, 403):
                raise ValueError(
                    f"ACESSO NEGADO AO SERVIDOR WEBDAV ({status}): {url}\n"
                    f"O token de acesso pode ter expirado. Atualize RFB_WEBDAV_URL."
                )
            elif status == 404:
                raise ValueError(
                    f"RECURSO NÃO ENCONTRADO NO WEBDAV ({status}): {url}\n"
                    f"O caminho pode ter mudado. Atualize RFB_WEBDAV_URL."
                )
            raise ValueError(f"ERRO HTTP {status} AO ACESSAR WEBDAV: {url}")

        return self._parse_multistatus(resp.content)

    @staticmethod
    def _parse_multistatus(content: bytes) -> List[Dict]:
        """
        Parseia a resposta XML multi-status do PROPFIND.

        :param content: conteúdo XML da resposta
        :return: lista de entradas com href, name, is_collection, size
        """
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise ValueError(f"RESPOSTA WEBDAV COM XML INVÁLIDO: {e}")

        entries = []
        for response in root.findall('d:response', _DAV_NS):
            href_el = response.find('d:href', _DAV_NS)
            if href_el is None or not href_el.text:
                continue

            href = unquote(href_el.text)

            # Procura propstat com status 200
            prop = None
            for propstat in response.findall('d:propstat', _DAV_NS):
                status_el = propstat.find('d:status', _DAV_NS)
                if status_el is not None and '200' in (status_el.text or ''):
                    prop = propstat.find('d:prop', _DAV_NS)
                    break

            # Fallback: primeiro propstat disponível
            if prop is None:
                propstat = response.find('d:propstat', _DAV_NS)
                if propstat is not None:
                    prop = propstat.find('d:prop', _DAV_NS)

            if prop is None:
                continue

            # Verifica se é uma coleção (diretório)
            is_collection = False
            resourcetype = prop.find('d:resourcetype', _DAV_NS)
            if resourcetype is not None:
                if resourcetype.find('d:collection', _DAV_NS) is not None:
                    is_collection = True

            # Tamanho do arquivo
            cl_el = prop.find('d:getcontentlength', _DAV_NS)
            size = 0
            if cl_el is not None and cl_el.text:
                try:
                    size = int(cl_el.text)
                except ValueError:
                    size = 0

            # Nome do recurso
            dn_el = prop.find('d:displayname', _DAV_NS)
            name = ''
            if dn_el is not None and dn_el.text:
                name = dn_el.text.strip()

            # Fallback: extrair nome do href
            if not name:
                clean_href = href.rstrip('/')
                name = clean_href.split('/')[-1] if clean_href else ''

            entries.append({
                'href': href,
                'name': name,
                'is_collection': is_collection,
                'size': size,
            })

        return entries

    def _available_months(self) -> Dict[str, str]:
        """
        Obtém os meses disponíveis para download via WebDAV PROPFIND.

        :return: Dicionário {MM/AAAA: AAAA-MM} ordenado decrescente.
        """
        entries = self._propfind(self.webdav_base_url)

        month_years = {}
        for entry in entries:
            if not entry['is_collection']:
                continue
            name = entry['name']
            if not name:
                continue
            # Valida formato AAAA-MM
            if re.match(r'^\d{4}-\d{2}$', name):
                year, month = name.split('-')
                mm_aaaa = f"{month}/{year}"
                month_years[mm_aaaa] = name

        if not month_years:
            raise ValueError(
                f"NENHUM PERÍODO DISPONÍVEL NO SERVIDOR WEBDAV ({self.webdav_base_url})"
            )

        # Ordena os meses em ordem decrescente
        sorted_month_years = dict(
            sorted(month_years.items(),
                   key=lambda item: self._parse_month(item[1]),
                   reverse=True)
        )

        return sorted_month_years

    def get_availabes(self):
        """
        Obtém os meses disponíveis para download.

        :return: String com os meses disponíveis.
        """
        month_years = self._available_months()
        availables = ", ".join(month_years.keys())
        return availables

    def get_latest(self):
        """
        Obtém o último mês disponível para download.

        :return: String com o último mês disponível.
        """
        month_years = self._available_months()
        latest = next(iter(month_years.keys()))
        return latest

    def get_metadata(self, month_year: Optional[str] = None) -> Dict[str, Dict[str, str]]:
        """
        Obtém as URLs e tamanhos dos arquivos de CNPJ via WebDAV PROPFIND.

        Diferente da abordagem anterior (HTML scraping + HEAD por arquivo),
        esta implementação obtém nome e tamanho em uma única requisição PROPFIND.

        :param month_year: string no formato MM/AAAA
        :return: Dicionário com metadados dos arquivos de CNPJ disponíveis.
        """
        # se o mês não foi especificado, usa o mais recente
        if month_year is None:
            month_year = self.get_latest()

        # verifica se o mês é válido
        elif not self._is_valid_period(month_year):
            raise ValueError(f"{month_year} NÃO É UM FORMATO VÁLIDO (MM/AAAA)")

        # verifica se o mês está disponível
        month_years_map = self._available_months()
        if month_year not in month_years_map:
            raise ValueError(f"{month_year} NÃO ESTÁ DISPONÍVEL PARA DOWNLOAD")

        folder = month_years_map[month_year]  # obtém o período no formato AAAA-MM
        folder_url = f"{self.webdav_base_url}{folder}/"

        # lista os arquivos do mês via PROPFIND
        entries = self._propfind(folder_url)

        # cria um dicionário com os metadados dos arquivos de CNPJ disponíveis
        result: Dict[str, Dict[str, str]] = {}
        for entry in entries:
            if entry['is_collection']:
                continue
            filename = entry['name']
            if not filename.lower().endswith('.zip'):
                continue

            file_url = f"{folder_url}{filename}"
            key = os.path.join(folder, filename)

            result[key] = {
                "month_year": month_year,
                "filename": filename,
                "file_url": file_url,
                "file_size": entry['size']
            }

        if not result:
            raise ValueError(
                f"NENHUM ARQUIVO ZIP ENCONTRADO PARA {month_year} EM {folder_url}"
            )

        # ordena os arquivos por nome
        result_sorted = dict(sorted(result.items(), key=lambda item: item[1]["filename"]))
        return result_sorted
