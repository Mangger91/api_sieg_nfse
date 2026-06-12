from __future__ import annotations

import base64
import binascii
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET

import requests

from config import (
    BACKOFF_INICIAL_SEGUNDOS,
    JANELA_RATE_LIMIT_SEGUNDOS,
    MAPA_TIPO_CAMPO_CNPJ,
    MAX_REQUISICOES_POR_MINUTO,
    MAX_TENTATIVAS_429,
    SIEG_API_HEADER,
    SIEG_API_KEY,
    SIEG_API_PREFIX,
    SIEG_URL_BAIXAR_XML,
    TAKE,
    TIMEOUT,
    TIPO_XML_NFSE,
)


XML_KEYS = (
    "Xml",
    "xml",
    "XML",
    "ConteudoXml",
    "conteudoXml",
    "ArquivoXml",
    "arquivoXml",
    "XmlBase64",
    "xmlBase64",
    "Base64",
    "base64",
    "Conteudo",
    "conteudo",
    "Arquivo",
    "arquivo",
)

NAME_KEYS = (
    "NomeArquivo",
    "nomeArquivo",
    "Nome",
    "nome",
    "Chave",
    "chave",
    "ChaveNFe",
    "chaveNFe",
    "Numero",
    "numero",
    "Id",
    "id",
)


@dataclass(frozen=True)
class XmlBaixado:
    nome_arquivo: str
    conteudo: bytes
    metadados: dict[str, Any]


class SiegApiError(Exception):
    pass


class RateLimiter:
    def __init__(self, max_requisicoes: int, janela_segundos: int) -> None:
        self.max_requisicoes = max_requisicoes
        self.janela_segundos = janela_segundos
        self._timestamps: list[float] = []

    def aguardar(self) -> None:
        agora = time.monotonic()
        inicio_janela = agora - self.janela_segundos
        self._timestamps = [ts for ts in self._timestamps if ts > inicio_janela]

        if len(self._timestamps) >= self.max_requisicoes:
            dormir = self.janela_segundos - (agora - self._timestamps[0])
            if dormir > 0:
                time.sleep(dormir)

        self._timestamps.append(time.monotonic())


class SiegClient:
    def __init__(self) -> None:
        token = f"{SIEG_API_PREFIX} {SIEG_API_KEY}".strip()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, application/xml, application/zip, */*",
                "Content-Type": "application/json",
                SIEG_API_HEADER: token,
            }
        )
        self.rate_limiter = RateLimiter(
            MAX_REQUISICOES_POR_MINUTO,
            JANELA_RATE_LIMIT_SEGUNDOS,
        )

    def baixar_xmls(
        self,
        cnpj: str,
        data_inicial,
        data_final,
        tipo: str,
    ) -> list[XmlBaixado]:
        if tipo not in MAPA_TIPO_CAMPO_CNPJ:
            raise ValueError(f"Tipo invalido: {tipo}")

        campo_cnpj = MAPA_TIPO_CAMPO_CNPJ[tipo]
        todos: list[XmlBaixado] = []
        skip = 0

        while True:
            payload = self._montar_payload(
                campo_cnpj=campo_cnpj,
                cnpj=cnpj,
                data_inicial=data_inicial,
                data_final=data_final,
                skip=skip,
            )
            resposta = self._post_com_retry(payload)
            pagina = list(self._extrair_xmls(resposta))
            todos.extend(pagina)

            if len(pagina) < TAKE:
                break

            skip += TAKE

        return todos

    def _montar_payload(
        self,
        campo_cnpj: str,
        cnpj: str,
        data_inicial,
        data_final,
        skip: int,
    ) -> dict[str, Any]:
        return {
            "TipoXml": TIPO_XML_NFSE,
            campo_cnpj: cnpj,
            "DataEmissaoInicio": data_inicial.strftime("%Y-%m-%d"),
            "DataEmissaoFim": data_final.strftime("%Y-%m-%d"),
            "Take": TAKE,
            "Skip": skip,
        }

    def _post_com_retry(self, payload: dict[str, Any]) -> requests.Response:
        backoff = BACKOFF_INICIAL_SEGUNDOS

        for tentativa in range(1, MAX_TENTATIVAS_429 + 1):
            self.rate_limiter.aguardar()
            response = self.session.post(
                SIEG_URL_BAIXAR_XML,
                json=payload,
                timeout=TIMEOUT,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                espera = int(retry_after) if retry_after and retry_after.isdigit() else backoff
                time.sleep(espera)
                backoff *= 2
                continue

            if 500 <= response.status_code < 600 and tentativa < MAX_TENTATIVAS_429:
                time.sleep(backoff)
                backoff *= 2
                continue

            if not response.ok:
                detalhe = response.text[:500]
                raise SiegApiError(
                    f"Erro SIEG HTTP {response.status_code}: {detalhe}"
                )

            return response

        raise SiegApiError("Limite de tentativas excedido ao consultar a SIEG.")

    def _extrair_xmls(self, response: requests.Response) -> Iterable[XmlBaixado]:
        content_type = response.headers.get("content-type", "").lower()
        body = response.content or b""

        if "zip" in content_type or zipfile.is_zipfile(BytesIO(body)):
            yield from _extrair_zip(body)
            return

        if _parece_xml(body):
            yield XmlBaixado("nfse.xml", body, {})
            return

        try:
            data = response.json()
        except ValueError as exc:
            raise SiegApiError("Resposta SIEG nao esta em JSON, XML ou ZIP.") from exc

        if isinstance(data, dict):
            if _dict_tem_xml(data):
                yield _xml_de_dict(data, 1)
                return

            for key in ("Items", "items", "Data", "data", "Resultados", "resultados"):
                value = data.get(key)
                if isinstance(value, list):
                    yield from _extrair_json_lista(value)
                    return

            return

        if isinstance(data, list):
            yield from _extrair_json_lista(data)


def salvar_xmls(xmls: Iterable[XmlBaixado], pasta_destino: Path) -> list[Path]:
    pasta_destino.mkdir(parents=True, exist_ok=True)
    caminhos: list[Path] = []

    for indice, xml in enumerate(xmls, start=1):
        nome = _nome_seguro(xml.nome_arquivo, indice)
        caminho = _caminho_unico(pasta_destino / nome)
        caminho.write_bytes(xml.conteudo)
        caminhos.append(caminho)

    return caminhos


def xml_valido(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 20:
            return False
        ET.parse(path)
        return True
    except Exception:
        return False


def _extrair_json_lista(items: list[Any]) -> Iterable[XmlBaixado]:
    for indice, item in enumerate(items, start=1):
        if isinstance(item, dict) and _dict_tem_xml(item):
            yield _xml_de_dict(item, indice)
        elif isinstance(item, str):
            yield XmlBaixado(f"nfse_{indice}.xml", _normalizar_xml_bytes(item), {})


def _dict_tem_xml(item: dict[str, Any]) -> bool:
    return any(key in item and item[key] for key in XML_KEYS)


def _xml_de_dict(item: dict[str, Any], indice: int) -> XmlBaixado:
    raw_xml = next(item[key] for key in XML_KEYS if key in item and item[key])
    raw_nome = next((item[key] for key in NAME_KEYS if key in item and item[key]), None)
    nome = str(raw_nome) if raw_nome else f"nfse_{indice}.xml"
    return XmlBaixado(nome, _normalizar_xml_bytes(raw_xml), item)


def _normalizar_xml_bytes(valor: Any) -> bytes:
    if isinstance(valor, bytes):
        return valor

    texto = str(valor).strip()
    if texto.startswith("<"):
        return texto.encode("utf-8")

    try:
        return base64.b64decode(texto, validate=True)
    except (binascii.Error, ValueError):
        return texto.encode("utf-8")


def _extrair_zip(body: bytes) -> Iterable[XmlBaixado]:
    with zipfile.ZipFile(BytesIO(body)) as arquivo_zip:
        for nome in arquivo_zip.namelist():
            if nome.lower().endswith(".xml"):
                yield XmlBaixado(Path(nome).name, arquivo_zip.read(nome), {})


def _parece_xml(body: bytes) -> bool:
    inicio = body.lstrip()[:20].lower()
    return inicio.startswith(b"<?xml") or inicio.startswith(b"<")


def _nome_seguro(nome: str, indice: int) -> str:
    nome = Path(str(nome).strip() or f"nfse_{indice}.xml").name
    nome = "".join(ch if ch not in '\\/:*?"<>|' else "-" for ch in nome)
    if not nome.lower().endswith(".xml"):
        nome += ".xml"
    return nome


def _caminho_unico(path: Path) -> Path:
    if not path.exists():
        return path

    for indice in range(1, 10000):
        candidato = path.with_name(f"{path.stem}_{indice}{path.suffix}")
        if not candidato.exists():
            return candidato

    return path.with_name(f"{path.stem}_{int(time.time())}{path.suffix}")
