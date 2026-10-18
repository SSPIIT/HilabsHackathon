@echo off
REM C5 quick test (20 NPIs). Run from the project root with the venv active.
set RATE=data\in-network-files\2026-09-30_centene-management-company-llc_healthnet-ca_in-network.json
python src\c5_extract_providers.py --input %RATE% --out data\interim\c3_providers.csv || exit /b 1
python src\c5_nppes_lookup.py --input data\interim\c3_providers.csv --out-dir data\interim --limit 20 || exit /b 1
python src\c5_build_candidates.py --providers data\interim\c3_providers.csv --nppes-dir data\interim --out-dir data\interim || exit /b 1
python src\c5_census_geocode.py --input data\interim\c5_addresses_to_geocode.csv --out data\interim\c5_geocoded.csv || exit /b 1
python src\c5_score_addresses.py --in-dir data\interim --zip-ref data\reference\ca_zip_reference.csv --out-dir data\output || exit /b 1
echo Done - see data\output\c5_npi_tin_address.csv
