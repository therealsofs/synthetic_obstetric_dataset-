
import pandas as pd
from pathlib import Path

# CONFIGURAÇÃO
FICHEIRO_ORIGINAL = Path("coorte_sintetica_5000x162v9.csv")
FICHEIRO_AMOSTRA = Path("amostra_200_pacientes.csv")
FICHEIRO_IDS = Path("ids_200_pacientes.csv")

N = 200
SEED = 42

# 1. Carregar CSV com deteção automática do separador
df = pd.read_csv(
    FICHEIRO_ORIGINAL,
    sep=None,
    engine="python",
    encoding="utf-8-sig"
)

# 2. Limpar nomes das colunas
df.columns = df.columns.str.strip().str.replace(
    "\ufeff", "", regex=False
)

print("Dimensões do dataset:", df.shape)
print("Primeiras colunas:", df.columns[:10].tolist())

# 3. Verificar número de pacientes
if len(df) < N:
    raise ValueError("O dataset tem menos de 200 registos.")

# 4. Seleção aleatória sem reposição
amostra = df.sample(
    n=N,
    replace=False,
    random_state=SEED
).reset_index(drop=True)

# 5. Guardar os 200 registos completos
amostra.to_csv(
    FICHEIRO_AMOSTRA,
    index=False,
    encoding="utf-8-sig"
)

# 6. Guardar identificadores, se disponíveis
if "patient_id" in df.columns:
    if df["patient_id"].isna().any() or not df["patient_id"].is_unique:
        raise ValueError("IDs em falta ou duplicados.")

    amostra[["patient_id"]].to_csv(
        FICHEIRO_IDS,
        index=False,
        encoding="utf-8-sig"
    )
else:
    # Guardar a posição original das linhas para rastreabilidade
    posicoes = pd.DataFrame({
        "linha_original_csv": amostra.index
    })
    print("AVISO: patient_id não encontrada.")
    print("Confirma os nomes das colunas apresentados acima.")

# 7. Confirmação final
print("\n--- RESULTADO ---")
print("Pacientes originais:", len(df))
print("Pacientes selecionadas:", len(amostra))
print("Variáveis preservadas:", len(amostra.columns))
print("Seed:", SEED)
print("Ficheiro criado:", FICHEIRO_AMOSTRA)
