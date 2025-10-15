import pandas as pd
from carregar_dados import carregar_fa, carregar_contrapartes, get_cnpj

print("Loading contrapartes (with fallback if files missing)...")
contrapartes = carregar_contrapartes()
print(f"Contrapartes: {len(contrapartes)} (sample shown): {contrapartes[:5]}")

print("\nLoading FA dataset (with fallback if files missing)...")
df = carregar_fa()
print(f"FA shape: {df.shape}")
print(df.head(3).to_string(index=False))
