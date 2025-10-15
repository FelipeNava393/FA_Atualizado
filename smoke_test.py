from __future__ import annotations

import os
import sys

# Ensure current dir in sys.path
sys.path.insert(0, os.getcwd())

import pandas as pd  # noqa: F401

from carregar_dados import carregar_contrapartes, carregar_fa, get_cnpj


def main():
    print("Loading contrapartes...")
    contras = carregar_contrapartes()
    print(f"Contrapartes: {len(contras)} found (sample: {contras[:5]})")

    print("Loading FA (no filter)...")
    df = carregar_fa()
    print(f"FA rows: {len(df)}; columns: {list(df.columns)}")

    if not df.empty:
        # Try filtering by first CNPJ if exists
        cnpj_col = "CNPJ"
        first_cnpj = df[cnpj_col].dropna().astype(str).str.strip().head(1).tolist()
        df2 = carregar_fa(cnpj_filtro=first_cnpj)
        print(f"FA filtered rows: {len(df2)} using CNPJ={first_cnpj}")

    print("OK")


if __name__ == "__main__":
    main()
