# API SIEG NFS-e

Projeto para baixar XMLs de NFS-e pela API SIEG, organizando os arquivos por
empresa, mes e tipo de nota.

## Instalar no servidor

```powershell
git clone https://github.com/Mangger91/api_sieg_nfse.git
cd api_sieg_nfse
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Em Linux:

```bash
git clone https://github.com/Mangger91/api_sieg_nfse.git
cd api_sieg_nfse
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configurar

Copie `.env.example` para `.env` e preencha a chave:

```env
SIEG_API_KEY=sua_chave_sieg
```

A planilha de empresas deve se chamar `credenciais_empresas.xlsx` por padrao e
ter, no minimo, as colunas:

```csv
Empresa,CNPJ
EMPRESA EXEMPLO LTDA,00000000000000
```

Tambem e possivel informar outro caminho:

```bash
python main.py --planilha caminho/empresas.xlsx
```

## Executar

Modo com argumentos:

```bash
python main.py --inicio 01/05/2026 --fim 31/05/2026 --tipo ambas
```

Modo interativo:

```bash
python main.py
```

Tipos aceitos:

- `emitidas`
- `recebidas`
- `ambas`

Os XMLs sao salvos em `downloads/`. Os controles de status sao salvos em `logs/`.

## Variaveis uteis

- `SIEG_API_KEY`: chave da API SIEG.
- `SIEG_API_HEADER`: cabecalho usado para autenticar. Padrao: `Authorization`.
- `SIEG_API_PREFIX`: prefixo opcional, como `Bearer`, se a conta exigir.
- `CAMINHO_PLANILHA`: caminho da planilha de empresas.
- `BASE_DOWNLOADS`: pasta de saida dos XMLs.
- `LOG_STATUS`: pasta dos arquivos de status.
- `TAKE`: quantidade por pagina da API. Padrao: `50`.
- `MAX_REQUISICOES_POR_MINUTO`: limite local de requisicoes. Padrao: `20`.

## Observacoes de seguranca

Nao versionar `.env`, `credenciais_empresas.xlsx`, `downloads/` ou `logs/`.
Esses arquivos podem conter chaves, CNPJs, XMLs fiscais e dados de clientes.
