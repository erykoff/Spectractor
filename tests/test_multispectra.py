import matplotlib as mpl
mpl.use('Agg')  # must be run first! But therefore requires noqa E402 on all other imports

from numpy.testing import run_module_suite  # noqa: E402
from spectractor.fit.fit_multispectra import _build_test_sample, MultiSpectraFitWorkspace, run_multispectra_minimisation
from spectractor import parameters
import numpy as np


NSPECTRA = 3
OZONE = 300
PWV = 5
AEROSOLS = 0.05

def test_multispectra():
    spectra = _build_test_sample(NSPECTRA, aerosols=AEROSOLS, ozone=OZONE, pwv=PWV)
    parameters.VERBOSE = True
    w = MultiSpectraFitWorkspace("./tests/data/", spectra, bin_width=5, verbose=True, fixed_deltas=True, fixed_A1s=False)
    run_multispectra_minimisation(w, method="newton", verbose=True, sigma_clip=10)

    nsigma = 2
    labels = ["VAOD_T", "OZONE", "PWV"]
    truth = [AEROSOLS, OZONE, PWV]
    indices = [0, 1, 2]
    ipar = w.params.get_free_parameters()  # non fixed param indices
    cov_indices = [list(ipar).index(k) for k in indices]  # non fixed param indices in cov matrix

    k = 0
    for i, l in zip(indices, labels):
        icov = cov_indices[k]
        w.my_logger.info(f"Test {l} best-fit {w.params.values[i]:.3f}+/-{np.sqrt(w.params.cov[icov, icov]):.3f} "
                            f"vs {truth[i]:.3f} at {nsigma}sigma level: "
                            f"{np.abs(w.params.values[i] - truth[i]) / np.sqrt(w.params.cov[icov, icov]) < nsigma}")
        assert np.abs(w.params.values[i] - truth[i]) / np.sqrt(w.params.cov[icov, icov]) < nsigma
        k += 1
    assert np.all(np.isclose(w.A1s, 1, atol=5e-3))


if __name__ == "__main__":
    run_module_suite()