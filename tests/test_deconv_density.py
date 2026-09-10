"""Real numerical checks for deconvolution density estimates and missing curves."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from scipy.stats import gaussian_kde

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.parse.deconv import fdr_density_distribution


@pytest.mark.parametrize('scores', [[], [0.7], [0.7] * 4, [np.nan, np.inf, -np.inf]])
@pytest.mark.parametrize('group', [0, 1])
def test_undefined_density_has_empty_numeric_schema(scores, group):
    data = pd.DataFrame({'TargetDecoyType': [group] * len(scores), 'Qscore': scores})
    for curve in fdr_density_distribution(data):
        assert curve.empty
        assert list(curve.columns) == ['x', 'y']
        assert all(dtype == np.dtype('float64') for dtype in curve.dtypes)


def test_variable_scores_preserve_scipy_estimate_and_ignore_nonfinite_values():
    target = [0.1, 0.4, 0.8]
    decoy = [0.2, 0.5, 0.6]
    data = pd.DataFrame({
        'TargetDecoyType': [0] * 5 + [1] * 4,
        'Qscore': target + [np.nan, np.inf] + decoy + [-np.inf],
    })
    for curve, scores in zip(fdr_density_distribution(data), (target, decoy)):
        expected_x = np.linspace(min(scores), max(scores), 200)
        np.testing.assert_array_equal(curve['x'], expected_x)
        np.testing.assert_allclose(curve['y'], gaussian_kde(scores)(expected_x), rtol=1e-14)
        assert np.isfinite(curve.to_numpy()).all()


def test_constant_target_does_not_hide_valid_decoy_density():
    data = pd.DataFrame({'TargetDecoyType': [0, 0, 1, 1], 'Qscore': [0.7, 0.7, 0.2, 0.4]})
    target, decoy = fdr_density_distribution(data)
    assert target.empty
    assert len(decoy) == 200
    assert (decoy['y'] > 0).all()
