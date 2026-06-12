from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional when deps are not installed yet
    load_dotenv = None


if load_dotenv:
    load_dotenv()


BASE_DIR = Path(__file__).resolve().parent

CAMINHO_PLANILHA = os.getenv("CAMINHO_PLANILHA", str(BASE_DIR / "credenciais_empresas.xlsx"))
BASE_DOWNLOADS = os.getenv("BASE_DOWNLOADS", str(BASE_DIR / "downloads"))
LOG_STATUS = os.getenv("LOG_STATUS", str(BASE_DIR / "logs"))

SIEG_API_KEY = os.getenv("SIEG_API_KEY", "").strip()
SIEG_API_HEADER = os.getenv("SIEG_API_HEADER", "Authorization").strip()
SIEG_API_PREFIX = os.getenv("SIEG_API_PREFIX", "").strip()
SIEG_URL_BAIXAR_XML = os.getenv("SIEG_URL_BAIXAR_XML", "https://api.sieg.com/BaixarXmls")

TIPO_XML_NFSE = int(os.getenv("TIPO_XML_NFSE", "3"))
TAKE = int(os.getenv("TAKE", "50"))
TIMEOUT = int(os.getenv("TIMEOUT", "90"))

MAX_REQUISICOES_POR_MINUTO = int(os.getenv("MAX_REQUISICOES_POR_MINUTO", "20"))
JANELA_RATE_LIMIT_SEGUNDOS = int(os.getenv("JANELA_RATE_LIMIT_SEGUNDOS", "60"))
MAX_TENTATIVAS_429 = int(os.getenv("MAX_TENTATIVAS_429", "5"))
BACKOFF_INICIAL_SEGUNDOS = int(os.getenv("BACKOFF_INICIAL_SEGUNDOS", "5"))

MAPA_TIPO_CAMPO_CNPJ = {
    "emitidas": os.getenv("SIEG_CAMPO_CNPJ_EMITIDAS", "CnpjRem"),
    "recebidas": os.getenv("SIEG_CAMPO_CNPJ_RECEBIDAS", "CnpjDest"),
}


class ConfiguracaoInvalida(Exception):
    pass


def validar_configuracao() -> None:
    if not SIEG_API_KEY:
        raise ConfiguracaoInvalida(
            "Defina a variavel de ambiente SIEG_API_KEY antes de executar."
        )


def limpar_nome(nome: object) -> str:
    nome = "" if pd.isna(nome) else str(nome)
    nome_limpo = re.sub(r'[\\/:*?"<>|]', "-", nome)
    nome_limpo = re.sub(r"\s+", " ", nome_limpo)
    return nome_limpo.strip() or "SEM_NOME"


def limpar_cnpj(cnpj: object) -> str:
    cnpj_limpo = re.sub(r"\D", "", "" if pd.isna(cnpj) else str(cnpj).strip())
    if not cnpj_limpo:
        return ""
    return cnpj_limpo.zfill(14)


def carregar_empresas(caminho_planilha: str) -> pd.DataFrame:
    caminho = Path(caminho_planilha)
    if not caminho.exists():
        raise FileNotFoundError(f"Planilha de empresas nao encontrada: {caminho}")

    df = pd.read_excel(caminho, dtype=str)
    colunas_obrigatorias = ["Empresa", "CNPJ"]
    faltando = [col for col in colunas_obrigatorias if col not in df.columns]

    if faltando:
        raise ValueError("Colunas obrigatorias ausentes: " + ", ".join(faltando))

    df["Empresa"] = df["Empresa"].fillna("").astype(str).str.strip()
    df["CNPJ"] = df["CNPJ"].apply(limpar_cnpj)
    df = df[(df["Empresa"] != "") & (df["CNPJ"] != "")]

    return df.drop_duplicates(subset=["CNPJ"]).reset_index(drop=True)


def montar_pasta_empresa(
    base_downloads: str,
    nome_empresa: str,
    data_referencia: datetime,
    tipo: str,
) -> Path:
    mes_ano = data_referencia.strftime("%m-%Y")
    subpasta = "Emitidas" if tipo == "emitidas" else "Recebidas"
    pasta = Path(base_downloads) / limpar_nome(nome_empresa) / mes_ano / subpasta
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def montar_nome_status_arquivo(data_inicial: datetime, tipo: str) -> Path:
    mes_ano = data_inicial.strftime("%m-%Y")
    rotulo = data_inicial.strftime("%m-%y")
    pasta = Path(LOG_STATUS) / mes_ano
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / f"{tipo}_{rotulo}.xlsx"


def carregar_status_periodo(caminho_arquivo: Path) -> pd.DataFrame:
    if caminho_arquivo.exists():
        df_status = pd.read_excel(caminho_arquivo, dtype=str)
    else:
        df_status = pd.DataFrame(
            columns=["Empresa", "CNPJ", "QtdNotas", "Status", "DataProcessamento"]
        )

    for col in ["Empresa", "CNPJ", "QtdNotas", "Status", "DataProcessamento"]:
        if col not in df_status.columns:
            df_status[col] = ""

    df_status["Empresa"] = df_status["Empresa"].fillna("").astype(str).str.strip()
    df_status["CNPJ"] = df_status["CNPJ"].apply(limpar_cnpj)
    df_status["Status"] = df_status["Status"].fillna("").astype(str).str.strip()
    df_status["QtdNotas"] = df_status["QtdNotas"].fillna("0").astype(str)
    df_status["DataProcessamento"] = (
        df_status["DataProcessamento"].fillna("").astype(str).str.strip()
    )

    return df_status


def garantir_linha_empresa(df_status: pd.DataFrame, empresa: str, cnpj: str) -> int:
    cnpj = limpar_cnpj(cnpj)
    mask = df_status["CNPJ"].eq(cnpj)

    if not mask.any():
        novo_idx = len(df_status)
        df_status.loc[novo_idx, "Empresa"] = empresa
        df_status.loc[novo_idx, "CNPJ"] = cnpj
        df_status.loc[novo_idx, "QtdNotas"] = 0
        df_status.loc[novo_idx, "Status"] = ""
        df_status.loc[novo_idx, "DataProcessamento"] = ""
        return novo_idx

    return int(df_status.index[mask][0])
