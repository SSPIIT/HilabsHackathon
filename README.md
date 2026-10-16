# centene-ptd-pipeline

Pipeline for parsing Centene price-transparency (TiC) files, identifying PPO networks, grading file quality, extracting rates, mapping providers to addresses, and building the final rate file.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Scope, paths and thresholds live in `config/config.yaml`.

## Data

`data/` is git-ignored (except `.gitkeep`). Place inputs under `data/raw/`:

| Folder | Contents |
|---|---|
| `index_files/` | Centene index JSONs |
| `rate_files/` | Downloaded in-network files |
| `nppes/` | NPPES NPI registry |
| `nucc/` | NUCC taxonomy |
| `reference/` | Specialty-to-billing-code Excel, CMS files |

## Running

Full pipeline (C1 → C6):

```bash
python run_pipeline.py
```

Individual steps:

```bash
python -m src.c1_parse_index         # index files -> data/interim/c1_index_raw.csv
python -m src.c1_clean_index         # cleaning / dedup
python -m src.c2_identify_ppo        # PPO network classification
python -m src.c3_grade_files         # file quality metrics
python -m src.c4_extract_rates       # stream rate files
python -m src.c5_address_mapping     # TIN -> NPI -> address
python -m src.c6_build_rate_file     # final Excel
python -m src.c7_cost_effectiveness  # cost-effectiveness analysis
```

## Docs

- `docs/logic.md` — deliverable 2
- `docs/file_quality_metrics.md` — deliverable 3
- `docs/summary.md` — deliverable 4
