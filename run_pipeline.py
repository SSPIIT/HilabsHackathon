"""Run C1 -> C6 in order."""
from src import (
    c1_parse_index,
    c1_clean_index,
    c2_identify_ppo,
    c3_grade_files,
    c4_extract_rates,
    c5_address_mapping,
    c6_build_rate_file,
)

STEPS = [
    c1_parse_index,
    c1_clean_index,
    c2_identify_ppo,
    c3_grade_files,
    c4_extract_rates,
    c5_address_mapping,
    c6_build_rate_file,
]


def main():
    for step in STEPS:
        print(f"==> {step.__name__}")
        step.main()


if __name__ == "__main__":
    main()
