"""The native acceptance's positive control uses retained scientific results."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental.accept_flashtnt import FIXTURE, check_identifications


def test_retained_aqpz_identification_is_a_positive_control():
    result = check_identifications(FIXTURE)
    assert result['top_accession'] == 'AQPZ'
    assert result['top_score'] == 505
    assert result['aqpz_tags'] > 0
    assert result['reference_fields_match']
