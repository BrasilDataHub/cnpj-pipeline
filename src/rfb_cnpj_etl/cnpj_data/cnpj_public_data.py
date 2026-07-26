# cnpj_data/cnpj_public_data.py

"""
Module for accessing the CNPJ data available on the Receita Federal WebDAV server (Nextcloud).

The Receita Federal migrated its files to a WebDAV server (Nextcloud), which makes it
necessary to use PROPFIND to list directories and GET to download files.

The base URL is configurable through the RFB_WEBDAV_URL environment variable.
"""

import os
import re
import requests
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from typing import Dict, List, Tuple, Optional
from ..config import CNPJ_WEBDAV_BASE_URL

# DAV namespace for XML parsing
_DAV_NS = {'d': 'DAV:'}

# Minimal body for PROPFIND — requests name, size and resource type
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
    Class for accessing the CNPJ data available on the Receita Federal WebDAV server.

    Uses PROPFIND to list directories and files. The download is done through a
    standard HTTP GET (WebDAV compatible).

    :params:
        webdav_base_url: WebDAV base URL for the CNPJ directory.
        _session: persistent HTTP session.
    """

    def __init__(self):
        self.webdav_base_url = CNPJ_WEBDAV_BASE_URL.rstrip('/') + '/'
        self._session = requests.Session()

    @staticmethod
    def _is_valid_period(month_year: str) -> bool:
        """
        Checks whether a string is in the MM/AAAA format.

        :param month_year: string in the MM/AAAA format
        :return: bool
        """
        match = re.match(r'^(\d{2})/(\d{4})$', month_year)
        return bool(match)

    @staticmethod
    def _parse_month(month_year: str) -> Tuple[int, int]:
        """
        Converts a string in the AAAA-MM format into a (year, month) pair.

        :param month_year: string in the AAAA-MM format
        :return: (year, month) pair
        """
        year_month = month_year.rstrip('/')
        try:
            year, month = year_month.split('-')
            return int(year), int(month)
        except ValueError:
            return 0, 0

    def _propfind(self, url: str, depth: int = 1) -> List[Dict]:
        """
        Runs a WebDAV PROPFIND request and returns the parsed entries.

        :param url: URL of the WebDAV resource
        :param depth: listing depth (0=self, 1=immediate children)
        :return: List of dictionaries with information about each entry
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
        Parses the multi-status XML response of the PROPFIND.

        :param content: XML content of the response
        :return: list of entries with href, name, is_collection, size
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

            # Looks for a propstat with status 200
            prop = None
            for propstat in response.findall('d:propstat', _DAV_NS):
                status_el = propstat.find('d:status', _DAV_NS)
                if status_el is not None and '200' in (status_el.text or ''):
                    prop = propstat.find('d:prop', _DAV_NS)
                    break

            # Fallback: first available propstat
            if prop is None:
                propstat = response.find('d:propstat', _DAV_NS)
                if propstat is not None:
                    prop = propstat.find('d:prop', _DAV_NS)

            if prop is None:
                continue

            # Checks whether it is a collection (directory)
            is_collection = False
            resourcetype = prop.find('d:resourcetype', _DAV_NS)
            if resourcetype is not None:
                if resourcetype.find('d:collection', _DAV_NS) is not None:
                    is_collection = True

            # File size
            cl_el = prop.find('d:getcontentlength', _DAV_NS)
            size = 0
            if cl_el is not None and cl_el.text:
                try:
                    size = int(cl_el.text)
                except ValueError:
                    size = 0

            # Resource name
            dn_el = prop.find('d:displayname', _DAV_NS)
            name = ''
            if dn_el is not None and dn_el.text:
                name = dn_el.text.strip()

            # Fallback: extract the name from the href
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
        Gets the months available for download through WebDAV PROPFIND.

        :return: Dictionary {MM/AAAA: AAAA-MM} sorted in descending order.
        """
        entries = self._propfind(self.webdav_base_url)

        month_years = {}
        for entry in entries:
            if not entry['is_collection']:
                continue
            name = entry['name']
            if not name:
                continue
            # Validates the AAAA-MM format
            if re.match(r'^\d{4}-\d{2}$', name):
                year, month = name.split('-')
                mm_aaaa = f"{month}/{year}"
                month_years[mm_aaaa] = name

        if not month_years:
            raise ValueError(
                f"NENHUM PERÍODO DISPONÍVEL NO SERVIDOR WEBDAV ({self.webdav_base_url})"
            )

        # Sorts the months in descending order
        sorted_month_years = dict(
            sorted(month_years.items(),
                   key=lambda item: self._parse_month(item[1]),
                   reverse=True)
        )

        return sorted_month_years

    def get_availables(self):
        """
        Gets the months available for download.

        :return: String with the available months.
        """
        month_years = self._available_months()
        availables = ", ".join(month_years.keys())
        return availables

    def get_latest(self):
        """
        Gets the latest month available for download.

        :return: String with the latest available month.
        """
        month_years = self._available_months()
        latest = next(iter(month_years.keys()))
        return latest

    def get_metadata(self, month_year: Optional[str] = None) -> Dict[str, Dict[str, str]]:
        """
        Gets the URLs and sizes of the CNPJ files through WebDAV PROPFIND.

        Unlike the previous approach (HTML scraping + one HEAD per file), this
        implementation gets name and size in a single PROPFIND request.

        :param month_year: string in the MM/AAAA format
        :return: Dictionary with metadata of the available CNPJ files.
        """
        # if the month was not specified, use the most recent one
        if month_year is None:
            month_year = self.get_latest()

        # check whether the month is valid
        elif not self._is_valid_period(month_year):
            raise ValueError(f"{month_year} NÃO É UM FORMATO VÁLIDO (MM/AAAA)")

        # check whether the month is available
        month_years_map = self._available_months()
        if month_year not in month_years_map:
            raise ValueError(f"{month_year} NÃO ESTÁ DISPONÍVEL PARA DOWNLOAD")

        folder = month_years_map[month_year]  # gets the period in the AAAA-MM format
        folder_url = f"{self.webdav_base_url}{folder}/"

        # lists the files of the month through PROPFIND
        entries = self._propfind(folder_url)

        # builds a dictionary with the metadata of the available CNPJ files
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

        # sorts the files by name
        result_sorted = dict(sorted(result.items(), key=lambda item: item[1]["filename"]))
        return result_sorted
