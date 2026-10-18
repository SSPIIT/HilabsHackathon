# Provider Rate Pipeline – Centene / Health Net California PPO

Turns Centene's Transparency in Coverage files into one clean provider rate file for the
**Health Net California commercial group PPO network**: who the provider is (NPI, TIN),
where they practise (address) and what they are paid (rate).

## Pipeline

| Step | What it does | Main output |
| --- | --- | --- |
| **C1** | Parse Centene index files into one table, remove duplicates | `data/interim/c1_plans.csv` |
| **C2** | Keep California, identify PPO plans (rule out HMO / EPO / POS) | `data/output/c2_ppo_plans_ca.csv` |
| **C3** | Download the in-network rate file, look up NPIs in NPPES, grade file quality | `data/output/c3_file_grades.csv` |
| **C4** | Extract rates, keep one rate per provider + service | `data/output/c4_rates.csv` |
| **C5** | Map each NPI + TIN to up to 3 scored practice addresses | `data/output/c5_npi_tin_address.csv` |
| **C6** | Join rates and addresses into the final file | `data/output/c6_provider_rate_file.csv` |
| **C7** | Cost-effectiveness metrics vs similar providers (defined in the C7 logic doc) | – |

Large files are streamed (never loaded whole), and stages that call outside services
(downloads, NPPES API, Census geocoder) resume from saved progress.

## Setup

Requires Python 3.12 and [Git LFS](https://git-lfs.com) (the source CSVs are stored with LFS).

```bash
git lfs install
git clone <repo-url>
cd HilabsHackathon
git lfs pull                      # only needed if LFS was installed after cloning

python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Run

The pipeline is defined in `dvc.yaml`. Stages whose inputs have not changed are skipped.

```bash
dvc repro                         # everything
dvc repro --glob "c3_*"           # one challenge (c1_* … c6_*)
dvc dag                           # show how the stages connect
```

Shared paths and settings are in `config/config.yaml`.

## Dashboard

An interactive dashboard (Streamlit) reads the pipeline outputs. Run the pipeline first.

```bash
streamlit run app.py
```

It opens in the browser at http://localhost:8501.

| View | For | What it shows |
| --- | --- | --- |
| **Executive** | A quick overview | Headline numbers, the funnel from Centene's index to the final file, address confidence and verification, how each rate was chosen, top specialties |
| **Analyst** | Detailed checks | Provider lookup (by NPI, TIN or name: addresses with sources and scores, every rate with the rule that kept it), code lookup (rate spread across providers), browse and filter the final file with CSV download, evidence tables for the address method |

The theme is set to light in `.streamlit/config.toml`.

## Data

```
data/raw/index_files/         Centene index files used (Health Net, Ambetter)
data/raw/extra_index_files/   QualChoice and Fidelis indexes, reviewed and out of scope
data/raw/sources/             Address sources: CMS Doctors & Clinicians, Medi-Cal FFS, CA facilities
data/raw/reference/           CMS 70 shoppable services (for C3)
data/in-network-files/        Downloaded rate file (~700 MB, fetched by C3)
data/interim/                 Intermediate tables
data/output/                  Results
```

Only **in-network** rate files are used: the PPO network is defined by negotiated rates,
while allowed-amount files report past out-of-network payments.

## Results

- **C2:** 583 California group PPO plans, all pointing to one rate file
- **C3:** file grade **A (96.2)**: precision 0.993, recall 0.852
- **C4:** 1.51M price rows → 28,730 provider + service rates
- **C5:** 99.8% of NPI + TIN pairs mapped to an address, 70.6% high confidence

## Documentation

- PPO network and address mapping logic (C2, C5)
- C3 – File Quality Metrics
- C7 – Rate Cost-Effectiveness Logic