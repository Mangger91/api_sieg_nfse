from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd

from api import SiegClient, salvar_xmls, xml_valido
from config import (
    BASE_DOWNLOADS,
    CAMINHO_PLANILHA,
    carregar_empresas,
    carregar_status_periodo,
    garantir_linha_empresa,
    montar_nome_status_arquivo,
    montar_pasta_empresa,
    validar_configuracao,
)


TIPOS_VALIDOS = ("emitidas", "recebidas")


def parse_data(valor: str) -> datetime:
    return datetime.strptime(valor.strip(), "%d/%m/%Y")


def obter_periodo_interativo() -> tuple[datetime, datetime]:
    data_inicial = parse_data(input("Digite a data inicial (DD/MM/AAAA): "))
    data_final = parse_data(input("Digite a data final (DD/MM/AAAA): "))
    return data_inicial, data_final


def obter_tipos_interativo() -> list[str]:
    escolha = input(
        "Digite '1' para NFS-e Emitidas, '2' para NFS-e Recebidas ou '3' para Ambas: "
    ).strip()

    if escolha == "1":
        return ["emitidas"]
    if escolha == "2":
        return ["recebidas"]
    if escolha == "3":
        return ["emitidas", "recebidas"]

    print("Escolha invalida. Digite '1', '2' ou '3'.")
    return obter_tipos_interativo()


def extrair_resumo_xml(path: Path) -> dict[str, str]:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {}

    def texto_tag(local_tag: str) -> str:
        for el in root.iter():
            if el.tag.split("}")[-1] == local_tag:
                return (el.text or "").strip()
        return ""

    return {
        "NUM_XML": path.name,
        "DATA_EMISSAO": texto_tag("dhEmi") or texto_tag("dEmi"),
        "COMPETENCIA": texto_tag("compet") or texto_tag("Competencia"),
        "CNPJ_PRESTADOR": texto_tag("CNPJ") or texto_tag("Cnpj"),
        "VALOR_SERVICO": texto_tag("vServ"),
        "VALOR_LIQUIDO": texto_tag("vLiq"),
        "CODIGO_SERVICO": texto_tag("cTribNac"),
    }


def salvar_relatorio_empresa(caminhos_xml: list[Path], pasta_empresa: Path, tipo: str) -> Path:
    registros = [extrair_resumo_xml(path) for path in caminhos_xml]
    df = pd.DataFrame(registros)

    colunas = [
        "NUM_XML",
        "DATA_EMISSAO",
        "COMPETENCIA",
        "CNPJ_PRESTADOR",
        "VALOR_SERVICO",
        "VALOR_LIQUIDO",
        "CODIGO_SERVICO",
    ]
    df = df.reindex(columns=colunas)

    caminho = pasta_empresa / f"relatorio_{tipo}.xlsx"
    df.to_excel(caminho, index=False)
    return caminho


def processar_empresa(
    client: SiegClient,
    empresa: str,
    cnpj: str,
    data_inicial: datetime,
    data_final: datetime,
    tipo: str,
) -> tuple[str, int]:
    pasta_empresa = montar_pasta_empresa(BASE_DOWNLOADS, empresa, data_inicial, tipo)
    xmls = client.baixar_xmls(cnpj, data_inicial, data_final, tipo)
    caminhos = salvar_xmls(xmls, pasta_empresa)

    caminhos_validos = [path for path in caminhos if xml_valido(path)]
    for path in set(caminhos) - set(caminhos_validos):
        path.rename(path.with_suffix(".invalid"))

    if caminhos_validos:
        relatorio = salvar_relatorio_empresa(caminhos_validos, pasta_empresa, tipo)
        print(f"Relatorio salvo em: {relatorio}")
        return "OK", len(caminhos_validos)

    return "SEM NOTAS", 0


def processar_tipo(
    client: SiegClient,
    df_empresas: pd.DataFrame,
    data_inicial: datetime,
    data_final: datetime,
    tipo: str,
    reprocessar: bool,
) -> None:
    caminho_status = montar_nome_status_arquivo(data_inicial, tipo)
    df_status = carregar_status_periodo(caminho_status)

    for _, row in df_empresas.iterrows():
        empresa = row["Empresa"]
        cnpj = row["CNPJ"]
        idx = garantir_linha_empresa(df_status, empresa, cnpj)
        status_atual = str(df_status.loc[idx, "Status"]).strip()

        if status_atual and not reprocessar:
            print(f"{empresa} ({cnpj}) ja possui status '{status_atual}', pulando.")
            continue

        print(f"\n=== Processando {tipo}: {empresa} ({cnpj}) ===")
        status = "ERRO"
        qtd_notas = 0

        try:
            status, qtd_notas = processar_empresa(
                client=client,
                empresa=empresa,
                cnpj=cnpj,
                data_inicial=data_inicial,
                data_final=data_final,
                tipo=tipo,
            )
        except Exception as exc:
            print(f"Erro ao processar {empresa}: {type(exc).__name__}: {exc}")

        df_status.loc[idx, "Empresa"] = empresa
        df_status.loc[idx, "CNPJ"] = cnpj
        df_status.loc[idx, "QtdNotas"] = int(qtd_notas)
        df_status.loc[idx, "Status"] = status
        df_status.loc[idx, "DataProcessamento"] = datetime.now().strftime(
            "%d/%m/%Y %H:%M:%S"
        )
        df_status.to_excel(caminho_status, index=False)

    print(f"Status salvo em: {caminho_status}")


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Baixa XMLs de NFS-e pela API SIEG.")
    parser.add_argument("--inicio", help="Data inicial no formato DD/MM/AAAA.")
    parser.add_argument("--fim", help="Data final no formato DD/MM/AAAA.")
    parser.add_argument(
        "--tipo",
        choices=("emitidas", "recebidas", "ambas"),
        help="Tipo de NFS-e a baixar.",
    )
    parser.add_argument(
        "--planilha",
        default=CAMINHO_PLANILHA,
        help="Caminho da planilha com as colunas Empresa e CNPJ.",
    )
    parser.add_argument(
        "--reprocessar",
        action="store_true",
        help="Reprocessa empresas que ja possuem status no periodo.",
    )
    return parser


def main() -> None:
    args = montar_parser().parse_args()
    validar_configuracao()

    if args.inicio and args.fim:
        data_inicial = parse_data(args.inicio)
        data_final = parse_data(args.fim)
    else:
        data_inicial, data_final = obter_periodo_interativo()

    if data_final < data_inicial:
        raise ValueError("A data final nao pode ser menor que a data inicial.")

    if args.tipo:
        tipos = list(TIPOS_VALIDOS) if args.tipo == "ambas" else [args.tipo]
    else:
        tipos = obter_tipos_interativo()

    df_empresas = carregar_empresas(args.planilha)
    client = SiegClient()

    for tipo in tipos:
        processar_tipo(
            client=client,
            df_empresas=df_empresas,
            data_inicial=data_inicial,
            data_final=data_final,
            tipo=tipo,
            reprocessar=args.reprocessar,
        )

    print("\nProcessamento finalizado.")


if __name__ == "__main__":
    main()
