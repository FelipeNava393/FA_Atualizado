from __future__ import annotations

import os
import random
import unicodedata
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

CONTRAPARTES_URL_DEFAULT = os.environ.get("CONTRAPARTES_URL", "data/contrapartes.xlsx")
FA_URL_DEFAULT = os.environ.get("FA_URL", "data/Resultados Ordinário_data.csv")


def _normalize_string(text: str) -> str:
    """Lowercase and strip accents for robust, case-insensitive comparisons."""
    if not isinstance(text, str):
        return str(text)
    normalized = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.lower().strip()


def _first_present_column(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    """Return the first column in df that matches any candidate (accent/case-insensitive)."""
    normalized_map = {_normalize_string(c): c for c in df.columns}
    for candidate in candidates:
        key = _normalize_string(candidate)
        if key in normalized_map:
            return normalized_map[key]
    return None


@lru_cache(maxsize=1)
def get_cnpj(contrapartes_url: str = CONTRAPARTES_URL_DEFAULT) -> List[Tuple[str, str]]:
    """Return list of (FORNECEDOR, CNPJ). Fallback to sample if file missing."""
    if os.path.exists(contrapartes_url):
        df = pd.read_excel(contrapartes_url, engine="openpyxl")
    else:
        df = _generate_sample_contrapartes()

    fornecedor_col = _first_present_column(df, ["FORNECEDOR", "Fornecedor", "Contraparte", "Empresa"]) or "FORNECEDOR"
    cnpj_col = _first_present_column(df, ["CNPJ"]) or "CNPJ"

    df = df[[fornecedor_col, cnpj_col]].copy()
    df.columns = ["FORNECEDOR", "CNPJ"]
    df["CNPJ"] = df["CNPJ"].astype(str).str.replace("\D", "", regex=True)
    return df.values.tolist()


@lru_cache(maxsize=1)
def carregar_contrapartes(contrapartes_url: str = CONTRAPARTES_URL_DEFAULT) -> List[str]:
    """Return list of fornecedores. Fallback to sample if file missing."""
    pairs = get_cnpj(contrapartes_url=contrapartes_url)
    fornecedores = [fornecedor for fornecedor, _ in pairs]
    fornecedores = sorted({f for f in fornecedores if isinstance(f, str) and f.strip()})
    return fornecedores


def carregar_fa(cnpj_filtro: Optional[List[str]] = None, fa_url: str = FA_URL_DEFAULT) -> pd.DataFrame:
    """Load and clean leverage factor dataset. Returns tidy DataFrame.

    Columns ensured:
      - 'Início do Período' (datetime)
      - 'Fator de Alavancagem' (float, percentage 0-100)
      - 'Sigla' (optional str)
      - 'FORNECEDOR' (optional str)
      - 'CNPJ' (str digits-only)
    """
    if os.path.exists(fa_url):
        try:
            df = pd.read_csv(fa_url, sep=";", low_memory=False)
        except Exception:
            # Try comma separator as fallback
            df = pd.read_csv(fa_url, sep=",", low_memory=False)
    else:
        df = _generate_sample_fa()

    # Drop known irrelevant columns if they exist
    drop_cols = [
        "Tipo de Cálculo",
        "Texto",
        "URL do Agente",
        "Mensagem link Agente",
    ]
    existing_drop = [c for c in drop_cols if c in df.columns]
    if existing_drop:
        df = df.drop(columns=existing_drop)

    # Standardize key columns
    inicio_col = _first_present_column(df, ["Início do Período", "Inicio do Período", "Inicio do Periodo", "Data"]) or "Início do Período"
    fa_col = _first_present_column(df, ["Fator de Alavancagem", "FA", "Fator"]) or "Fator de Alavancagem"
    sigla_col = _first_present_column(df, ["Sigla", "Ticker", "Código"]) or None
    fornecedor_col = _first_present_column(df, ["FORNECEDOR", "Fornecedor", "Contraparte", "Empresa"]) or None
    cnpj_col = _first_present_column(df, ["CNPJ"]) or None

    # Ensure presence of expected columns
    for col in [inicio_col, fa_col]:
        if col not in df.columns:
            raise ValueError(f"Coluna obrigatória ausente no dataset: {col}")

    # Parse dates and FA numeric
    df[inicio_col] = pd.to_datetime(df[inicio_col], errors="coerce", dayfirst=True)

    # Clean FA: accept '1,23', '1.23', blanks, NaN -> percent
    fa_series = df[fa_col].astype(str).str.strip().replace({"": np.nan})
    fa_series = fa_series.str.replace("%", "", regex=False)
    fa_series = fa_series.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    # Now fa_series may be like 123 or 1.23; assume data is in factor form or percent; heuristic:
    fa_numeric = pd.to_numeric(fa_series, errors="coerce")
    # If median seems like 0-1 range, convert to percent; else assume already percent
    median_val = fa_numeric.median(skipna=True)
    if pd.notna(median_val) and 0 <= median_val <= 1.5:
        fa_numeric = fa_numeric * 100.0
    df[fa_col] = fa_numeric.fillna(0.0)

    # Normalize optional columns
    if cnpj_col and cnpj_col in df.columns:
        df[cnpj_col] = df[cnpj_col].astype(str).str.replace("\D", "", regex=True)
    else:
        df["CNPJ"] = ""
        cnpj_col = "CNPJ"

    if fornecedor_col and fornecedor_col in df.columns:
        df.rename(columns={fornecedor_col: "FORNECEDOR"}, inplace=True)
    else:
        df["FORNECEDOR"] = ""

    if sigla_col and sigla_col in df.columns:
        df.rename(columns={sigla_col: "Sigla"}, inplace=True)
    else:
        df["Sigla"] = ""

    if inicio_col != "Início do Período":
        df.rename(columns={inicio_col: "Início do Período"}, inplace=True)
    if fa_col != "Fator de Alavancagem":
        df.rename(columns={fa_col: "Fator de Alavancagem"}, inplace=True)

    # Basic filter by CNPJ
    if cnpj_filtro and len(cnpj_filtro) > 0:
        filtered = set(str(c).replace("\D", "") for c in cnpj_filtro)
        df = df[df[cnpj_col].isin(filtered)]

    # Sort for nicer UX
    df = df.sort_values(["Início do Período", "FORNECEDOR", "Sigla"], na_position="last").reset_index(drop=True)

    return df


# ---------- Sample Data Generators (fallbacks) ----------

def _generate_sample_contrapartes(n: int = 12) -> pd.DataFrame:
    rng = random.Random(42)
    fornecedores = [f"Empresa {i:02d}" for i in range(1, n + 1)]
    cnpjs = [f"{rng.randrange(10**14, 10**15-1)}" for _ in fornecedores]
    return pd.DataFrame({"FORNECEDOR": fornecedores, "CNPJ": cnpjs})


def _generate_sample_fa(n_empresas: int = 12, periods: int = 24) -> pd.DataFrame:
    rng = np.random.default_rng(123)
    base_dates = pd.date_range("2022-01-01", periods=periods, freq="MS")
    fornecedores = [f"Empresa {i:02d}" for i in range(1, n_empresas + 1)]
    siglas = [f"EMP{i:02d}" for i in range(1, n_empresas + 1)]
    rows = []
    for fornecedor, sigla in zip(fornecedores, siglas):
        cnpj = str(np.random.randint(10**13, 10**14 - 1)).zfill(14)
        levels = rng.normal(loc=1.2, scale=0.3, size=len(base_dates))  # around 120%
        for dt, level in zip(base_dates, levels):
            rows.append(
                {
                    "Início do Período": dt,
                    "Fator de Alavancagem": max(0.0, float(level) * 100.0),
                    "Sigla": sigla,
                    "FORNECEDOR": fornecedor,
                    "CNPJ": cnpj,
                }
            )
    df = pd.DataFrame(rows)
    return df
