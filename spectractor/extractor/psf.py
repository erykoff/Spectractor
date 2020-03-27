import sys
import numpy as np
import matplotlib.pyplot as plt
from deprecated import deprecated

from scipy.optimize import basinhopping, minimize
from scipy.interpolate import interp1d, interp2d
from scipy.integrate import quad
from iminuit import Minuit

from astropy.modeling import Fittable1DModel, Fittable2DModel, Parameter
from astropy.table import Table

from spectractor.tools import (dichotomie, fit_poly1d, plot_image_simple, compute_fwhm)
from spectractor.extractor.background import extract_spectrogram_background_sextractor
from spectractor import parameters
from spectractor.config import set_logger
from spectractor.fit.fitter import FitWorkspace, run_minimisation, run_minimisation_sigma_clipping


class PSF:
    """Generic PSF model class.

    The PSF models must contain at least the "amplitude", "x_mean" and "y_mean" parameters as the first three parameters
    (in this order) and "saturation" parameter as the last parameter. "amplitude", "x_mean" and "y_mean"
    stands respectively for the general amplitude of the model, the position along the dispersion axis and the
    transverse position with respect to the dispersion axis (assumed to be the X axis).
    Last "saturation" parameter must be express in the same units as the signal to model and as the "amplitude"
    parameter. The PSF models must be normalized to one in total flux divided by the first parameter (amplitude).
    Then the PSF model integral is equal to the "amplitude" parameter.

    """

    def __init__(self):
        self.my_logger = set_logger(self.__class__.__name__)
        self.p = np.array([])
        self.param_names = ["amplitude", "x_mean", "y_mean", "saturation"]
        self.axis_names = ["$A$", r"$x_0$", r"$y_0$", "saturation"]
        self.bounds_soft = [[]]
        self.bounds_hard = [[]]
        self.p_default = np.array([1, 0, 0, 1])
        self.max_half_width = np.inf

    def evaluate(self, pixels, p=None):
        if p is not None:
            self.p = p
        # amplitude, x_mean, y_mean, saturation = self.p
        if pixels.ndim == 3 and pixels.shape[0] == 2:
            return np.zeros_like(pixels)
        elif pixels.ndim == 1:
            return np.zeros_like(pixels)
        else:
            self.my_logger.error(f"\n\tPixels array must have dimension 1 or shape=(2,Nx,Ny). "
                                 f"Here pixels.ndim={pixels.shape}.")
            return None

    def apply_max_width_to_bounds(self, max_half_width=None):
        pass

    def fit_psf(self, data, data_errors=None, bgd_model_func=None):
        """
        Fit a PSF model on 1D or 2D data.

        Parameters
        ----------
        data: array_like
            1D or 2D array containing the data.
        data_errors: np.array, optional
            The 1D or 2D array of uncertainties.
        bgd_model_func: callable, optional
            A 1D or 2D function to model the extracted background (default: None -> null background).

        Returns
        -------
        fit_workspace: PSFFitWorkspace
            The PSFFitWorkspace instance to get info about the fitting.

        Examples
        --------

        Build a mock PSF2D without background and with random Poisson noise:

        >>> p0 = np.array([200000, 20, 30, 5, 2, -0.1, 2, 400000])
        >>> psf0 = MoffatGauss(p0)
        >>> xx, yy = np.mgrid[:50, :60]
        >>> data = psf0.evaluate(np.array([xx, yy]), p0)
        >>> data += psf0.evaluate(np.array([xx, yy]), p=[20000, 20, 50, 5, 2, -0.1, 2, 400000])
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        Fit the data in 2D:

        >>> p = np.array([100000, 19, 31, 3, 3, -0.1, 2, 400000])
        >>> psf = MoffatGauss(p)
        >>> w = psf.fit_psf(data, data_errors=data_errors, bgd_model_func=None)
        >>> w.plot_fit()

        ..  doctest::
            :hide:

            >>> assert w.model is not None
            >>> residuals = (w.data-w.model)/w.err
            >>> assert w.costs[-1] / w.pixels.size < 1.2
            >>> assert np.abs(np.mean(residuals)) < 0.2
            >>> assert np.std(residuals) < 1.2

        Fit the data in 1D:

        >>> data1d = data[:,int(p[1])]
        >>> data1d_err = data_errors[:,int(p[1])]
        >>> p = np.array([100000, 15, 35, 5, 2, -0.1, 2, 400000])
        >>> psf = MoffatGauss(p)
        >>> w = psf.fit_psf(data1d, data_errors=data1d_err, bgd_model_func=None)
        >>> w.plot_fit()

        ..  doctest::
            :hide:

            >>> assert w.model is not None
            >>> residuals = (w.data-w.model)/w.err
            >>> assert w.costs[-1] / w.pixels.size < 1.2
            >>> assert np.abs(np.mean(residuals)) < 0.15
            >>> assert np.std(residuals) < 1.2

        .. plot::
            :include-source:

            import numpy as np
            import matplotlib.pyplot as plt
            from spectractor.extractor.psf import *
            p = np.array([200000, 20, 30, 5, 2, -0.1, 2, 400000])
            psf = MoffatGauss(p)
            xx, yy = np.mgrid[:50, :60]
            data = psf.evaluate(np.array([xx, yy]), p)
            data = np.random.poisson(data)
            data_errors = np.sqrt(data+1)
            data = np.random.poisson(data)
            data_errors = np.sqrt(data+1)
            psf = MoffatGauss(p)
            w = psf.fit_psf(data, data_errors=data_errors, bgd_model_func=None)
            w.plot_fit()

        """
        w = PSFFitWorkspace(self, data, data_errors, bgd_model_func=bgd_model_func,
                            verbose=False, live_fit=False)
        run_minimisation(w, method="newton", ftol=1 / w.pixels.size, xtol=1e-6, niter=50, fix=w.fixed)
        self.p = w.psf.p
        return w


class MoffatGauss(PSF):

    def __init__(self, p=None):
        PSF.__init__(self)
        self.p_default = np.array([1, 0, 0, 3, 2, 0, 1, 1])
        if p is not None:
            self.p = p
        else:
            self.p = np.copy(self.p_default)
        self.param_names = ["amplitude", "x_mean", "y_mean", "gamma", "alpha", "eta_gauss", "stddev",
                            "saturation"]
        self.axis_names = ["$A$", r"$x_0$", r"$y_0$", r"$\gamma$", r"$\alpha$", r"$\eta$", r"$\sigma$", "saturation"]
        self.bounds_hard = np.array([(0, np.inf), (-np.inf, np.inf), (-np.inf, np.inf), (0.1, np.inf), (1.1, 10),
                                     (-1, 0), (0.1, np.inf), (0, np.inf)])
        self.bounds_soft = np.array([(0, np.inf), (-np.inf, np.inf), (-np.inf, np.inf), (0.1, np.inf), (1.1, 10),
                                     (-1, 0), (0.1, np.inf), (0, np.inf)])

    def apply_max_width_to_bounds(self, max_half_width=None):
        if max_half_width is not None:
            self.max_half_width = max_half_width
        self.bounds_hard = np.array([(0, np.inf), (-np.inf, np.inf), (0, 2*self.max_half_width),
                                     (0.1, self.max_half_width), (1.1, 10), (-1, 0), (0.1, self.max_half_width),
                                     (0, np.inf)])
        self.bounds_soft = np.array([(0, np.inf), (-np.inf, np.inf), (0, 2*self.max_half_width),
                                     (0.1, self.max_half_width), (1.1, 10), (-1, 0), (0.1, self.max_half_width),
                                     (0, np.inf)])

    def evaluate(self, pixels, p=None):
        """Evaluate the MoffatGauss function.

        The function is normalized to have an integral equal to amplitude parameter.

        Parameters
        ----------
        pixels: list
            List containing the X abscisse 2D array and the Y abscisse 2D array.
        p: array_like
            The parameter array. If None, the array used to instanciate the class is taken.
            If given, the class instance parameter array is updated.

        Returns
        -------
        output: array_like
            The PSF function evaluated.

        Examples
        --------
        >>> p = [2,20,30,4,2,-0.5,1,10]
        >>> psf = MoffatGauss(p)
        >>> xx, yy = np.mgrid[:50, :60]
        >>> out = psf.evaluate(pixels=np.array([xx, yy]))

        .. plot::

            import matplotlib.pyplot as plt
            import numpy as np
            from spectractor.extractor.psf import PSF2D
            p = [2,20,30,4,2,-0.5,1,10]
            psf = PSF2D(p)
            xx, yy = np.mgrid[:50, :60]
            out = psf.evaluate(pixels=np.array([xx, yy]))
            fig = plt.figure(figsize=(5,5))
            plt.imshow(out, origin="lower")
            plt.xlabel("X [pixels]")
            plt.ylabel("Y [pixels]")
            plt.show()

        """
        if p is not None:
            self.p = p
        amplitude, x_mean, y_mean, gamma, alpha, eta_gauss, stddev, saturation = self.p
        if pixels.ndim == 3 and pixels.shape[0] == 2:
            x, y = pixels
            rr = ((x - x_mean) ** 2 + (y - y_mean) ** 2)
            rr_gg = rr / (gamma * gamma)
            a = ((1 + rr_gg) ** (-alpha) + eta_gauss * np.exp(-(rr / (2. * stddev * stddev))))
            norm = (np.pi * gamma * gamma) / (alpha - 1) + eta_gauss * 2 * np.pi * stddev * stddev
            a *= amplitude / norm
            return np.clip(a, 0, saturation).T
        elif pixels.ndim == 1:
            y = pixels
            rr = (y - y_mean) * (y - y_mean)
            rr_gg = rr / (gamma * gamma)
            try:
                a = ((1 + rr_gg) ** (-alpha) + eta_gauss * np.exp(-(rr / (2. * stddev * stddev))))
            except RuntimeWarning:  # pragma: no cover
                my_logger = set_logger(__name__)
                my_logger.warning(f"{[amplitude, y_mean, gamma, alpha, eta_gauss, stddev, saturation]}")
                a = eta_gauss * np.exp(-(rr / (2. * stddev * stddev)))
            # integral = compute_integral(x, a) #, bounds=(-10*fwhm, 10*fwhm))
            dx = np.gradient(y)[0]
            integral = np.sum(a) * dx
            norm = amplitude
            if integral != 0:
                norm /= integral
            a *= norm
            return np.clip(a, 0, saturation).T
        else:
            self.my_logger.error(f"\n\tPixels array must have dimension 1 or shape=(2,Nx,Ny). "
                                 f"Here pixels.ndim={pixels.shape}.")
            return None


class PSFFitWorkspace(FitWorkspace):
    """Generic PSF fitting workspace.

    """

    def __init__(self, psf, data, data_errors, bgd_model_func=None, file_name="",
                 nwalkers=18, nsteps=1000, burnin=100, nbins=10,
                 verbose=0, plot=False, live_fit=False, truth=None):
        """

        Parameters
        ----------
        psf
        data: array_like
            The data array (background subtracted) of dimension 1 or 2.
        data_errors
        bgd_model_func
        file_name
        nwalkers
        nsteps
        burnin
        nbins
        verbose
        plot
        live_fit
        truth

        Examples
        --------

        Build a mock spectrogram with random Poisson noise:

        >>> p = np.array([100, 50, 50, 3, 2, -0.1, 2, 200])
        >>> psf = MoffatGauss(p)
        >>> data = psf.evaluate(p)
        >>> data_errors = np.sqrt(data+1)

        Fit the data:

        >>> w = PSFFitWorkspace(psf, data, data_errors, bgd_model_func=None, verbose=True)

        """
        FitWorkspace.__init__(self, file_name, nwalkers, nsteps, burnin, nbins, verbose, plot,
                              live_fit, truth=truth)
        self.my_logger = set_logger(self.__class__.__name__)
        if data.shape != data_errors.shape:
            self.my_logger.error(f"\n\tData and uncertainty arrays must have the same shapes. "
                                 f"Here data.shape={data.shape} and data_errors.shape={data_errors.shape}.")
        self.psf = psf
        self.data = data
        self.err = data_errors
        self.bgd_model_func = bgd_model_func
        self.p = np.copy(self.psf.p[1:])
        self.guess = np.copy(self.psf.p)
        self.saturation = self.psf.p[-1]
        self.fixed = [False] * len(self.p)
        self.fixed[-1] = True  # fix saturation parameter
        self.input_labels = list(np.copy(self.psf.param_names[1:]))
        self.axis_names = list(np.copy(self.psf.axis_names[1:]))
        self.bounds = self.psf.bounds_hard[1:]
        self.nwalkers = max(2 * self.ndim, nwalkers)

        # prepare the fit
        if data.ndim == 2:
            self.Ny, self.Nx = self.data.shape
            self.psf.apply_max_width_to_bounds(self.Ny//2)
            self.pixels = np.mgrid[:self.Nx, :self.Ny]
        elif data.ndim == 1:
            self.Ny = self.data.size
            self.Nx = 1
            self.psf.apply_max_width_to_bounds(self.Ny//2)
            self.pixels = np.arange(self.Ny)
            self.fixed[0] = True
        else:
            self.my_logger.error(f"\n\tData array must have dimension 1 or 2. Here pixels.ndim={data.ndim}.")

        # update bounds
        self.bounds = self.psf.bounds_hard[1:]

        # error matrix
        self.W = 1. / (self.err * self.err)
        self.W = np.diag(self.W.flatten())
        self.W_dot_data = self.W @ self.data.flatten()

    def simulate(self, *shape_params):
        """
        Compute a PSF model given PSF parameters and minimizing
        amplitude parameter given a data array.

        Parameters
        ----------
        shape_params: array_like
            PSF shape parameter array (all parameters except amplitude).

        Examples
        --------

        Build a mock PSF2D without background and with random Poisson noise:

        >>> p = np.array([200000, 20, 30, 5, 2, -0.1, 2, 400000])
        >>> psf = MoffatGauss(p)
        >>> xx, yy = np.mgrid[:50, :60]
        >>> data = psf.evaluate(np.array([xx, yy]), p)
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        Fit the data in 2D:

        >>> w = PSFFitWorkspace(psf, data, data_errors, bgd_model_func=None, verbose=True)
        >>> x, mod, mod_err = w.simulate(*p[1:])
        >>> w.plot_fit()

        ..  doctest::
            :hide:

            >>> assert mod is not None
            >>> assert np.mean(np.abs(mod-data)/data_errors) < 1

        Fit the data in 1D:

        >>> data1d = data[:,int(p[1])]
        >>> data1d_err = data_errors[:,int(p[1])]
        >>> w = PSFFitWorkspace(psf, data1d, data1d_err, bgd_model_func=None, verbose=True)
        >>> x, mod, mod_err = w.simulate(*p[1:])
        >>> w.plot_fit()

        ..  doctest::
            :hide:

            >>> assert mod is not None
            >>> assert np.mean(np.abs(mod-data1d)/data1d_err) < 1

        .. plot::

            import numpy as np
            import matplotlib.pyplot as plt
            from spectractor.extractor.psf import *
            p = np.array([2000, 20, 30, 5, 2, -0.1, 2, 400])
            psf = MoffatGauss(p)
            xx, yy = np.mgrid[:50, :60]
            data = psf.evaluate(np.array([xx, yy]), p)
            data = np.random.poisson(data)
            data_errors = np.sqrt(data+1)
            data = np.random.poisson(data)
            data_errors = np.sqrt(data+1)
            w = PSFFitWorkspace(psf, data, data_errors, bgd_model_func=bgd_model_func, verbose=True)
            x, mod, mod_err = w.simulate(*p[:-1])
            w.plot_fit()

        """
        # Initialization of the regression
        self.p = np.copy(shape_params)
        # Matrix filling
        M = self.psf.evaluate(self.pixels, p=np.array([1] + list(self.p))).flatten()
        M_dot_W_dot_M = M.T @ self.W @ M
        # Regression
        amplitude = M.T @ self.W_dot_data / M_dot_W_dot_M
        # Save results
        self.model = self.psf.evaluate(self.pixels, p=np.array([amplitude] + list(self.p)))
        self.model_err = np.zeros_like(self.model)
        return self.pixels, self.model, self.model_err

    def plot_fit(self):
        fig = plt.figure()
        if self.data.ndim == 1:
            fig, ax = plt.subplots(2, 1, figsize=(6, 6), sharex='all', gridspec_kw={'height_ratios': [5, 1]})
            data = np.copy(self.data)
            if self.bgd_model_func is not None:
                data = data + self.bgd_model_func(self.pixels)
            ax[0].errorbar(self.pixels, data, yerr=self.err, fmt='ro', label="Data")
            if len(self.outliers) > 0:
                ax[0].errorbar(self.outliers, data[self.outliers], yerr=self.err[self.outliers], fmt='go',
                               label=rf"Outliers ({self.sigma_clip}$\sigma$)")
            if self.bgd_model_func is not None:
                ax[0].plot(self.pixels, self.bgd_model_func(self.pixels), 'b--', label="fitted bgd")
            if self.guess is not None:
                if self.bgd_model_func is not None:
                    ax[0].plot(self.pixels, self.psf.evaluate(self.pixels, p=self.guess)
                               + self.bgd_model_func(self.pixels), 'k--', label="Guess")
                else:
                    ax[0].plot(self.pixels, self.psf.evaluate(self.pixels, p=self.guess),
                               'k--', label="Guess")
            model = np.copy(self.model)
            # if self.bgd_model_func is not None:
            #    model = self.model + self.bgd_model_func(self.pixels)
            ax[0].plot(self.pixels, model, 'b-', label="Model")
            ylim = list(ax[0].get_ylim())
            ylim[1] = 1.2 * np.max(self.model)
            ax[0].set_ylim(ylim)
            ax[0].set_ylabel('Transverse profile')
            ax[0].legend(loc=2, numpoints=1)
            ax[0].grid(True)
            txt = ""
            for ip, p in enumerate(self.input_labels):
                txt += f'{p}: {self.p[ip]:.4g}\n'
            ax[0].text(0.95, 0.95, txt, horizontalalignment='right', verticalalignment='top', transform=ax[0].transAxes)
            # residuals
            residuals = (data - model) / self.err
            residuals_err = np.ones_like(self.err)
            ax[1].errorbar(self.pixels, residuals, yerr=residuals_err, fmt='ro')
            if len(self.outliers) > 0:
                residuals_outliers = (data[self.outliers] - model[self.outliers]) / self.err[self.outliers]
                residuals_outliers_err = np.ones_like(residuals_outliers)
                ax[1].errorbar(self.outliers, residuals_outliers, yerr=residuals_outliers_err, fmt='go')
            ax[1].axhline(0, color='b')
            ax[1].grid(True)
            std = np.std(residuals)
            ax[1].set_ylim([-3. * std, 3. * std])
            ax[1].set_xlabel(ax[0].get_xlabel())
            ax[1].set_ylabel('(data-fit)/err')
            ax[0].set_xticks(ax[1].get_xticks()[1:-1])
            ax[0].get_yaxis().set_label_coords(-0.1, 0.5)
            ax[1].get_yaxis().set_label_coords(-0.1, 0.5)
            # fig.tight_layout()
            # fig.subplots_adjust(wspace=0, hspace=0)
        elif self.data.ndim == 2:
            gs_kw = dict(width_ratios=[3, 0.15], height_ratios=[1, 1, 1, 1])
            fig, ax = plt.subplots(nrows=4, ncols=2, figsize=(5, 7), gridspec_kw=gs_kw)
            norm = np.max(self.data)
            plot_image_simple(ax[0, 0], data=self.model / norm, aspect='auto', cax=ax[0, 1], vmin=0, vmax=1,
                              units='1/max(data)')
            ax[0, 0].set_title("Model", fontsize=10, loc='center', color='white', y=0.8)
            plot_image_simple(ax[1, 0], data=self.data / norm, title='Data', aspect='auto',
                              cax=ax[1, 1], vmin=0, vmax=1, units='1/max(data)')
            ax[1, 0].set_title('Data', fontsize=10, loc='center', color='white', y=0.8)
            residuals = (self.data - self.model)
            # residuals_err = self.spectrum.spectrogram_err / self.model
            norm = self.err
            residuals /= norm
            std = float(np.std(residuals))
            plot_image_simple(ax[2, 0], data=residuals, vmin=-5 * std, vmax=5 * std, title='(Data-Model)/Err',
                              aspect='auto', cax=ax[2, 1], units='', cmap="bwr")
            ax[2, 0].set_title('(Data-Model)/Err', fontsize=10, loc='center', color='black', y=0.8)
            ax[2, 0].text(0.05, 0.05, f'mean={np.mean(residuals):.3f}\nstd={np.std(residuals):.3f}',
                          horizontalalignment='left', verticalalignment='bottom',
                          color='black', transform=ax[2, 0].transAxes)
            ax[0, 0].set_xticks(ax[2, 0].get_xticks()[1:-1])
            ax[0, 1].get_yaxis().set_label_coords(3.5, 0.5)
            ax[1, 1].get_yaxis().set_label_coords(3.5, 0.5)
            ax[2, 1].get_yaxis().set_label_coords(3.5, 0.5)
            ax[3, 1].remove()
            ax[3, 0].plot(np.arange(self.Nx), self.data.sum(axis=0), label='Data')
            ax[3, 0].plot(np.arange(self.Nx), self.model.sum(axis=0), label='Model')
            ax[3, 0].set_ylabel('Transverse sum')
            ax[3, 0].set_xlabel(r'X [pixels]')
            ax[3, 0].legend(fontsize=7)
            ax[3, 0].grid(True)
        else:
            self.my_logger.error(f"\n\tData array must have dimension 1 or 2. Here data.ndim={self.data.ndim}.")
        if self.live_fit:  # pragma: no cover
            plt.draw()
            plt.pause(1e-8)
            plt.close()
        else:
            if parameters.DISPLAY:
                plt.show()
            else:
                plt.close(fig)
        if parameters.SAVE:  # pragma: no cover
            figname = self.filename.replace(self.filename.split('.')[-1], "_bestfit.pdf")
            self.my_logger.info(f"\n\tSave figure {figname}.")
            fig.savefig(figname, dpi=100, bbox_inches='tight')


class ChromaticPSF:
    """Class to store a PSF evolving with wavelength.

    The wavelength evolution is stored in an Astropy table instance. Whatever the PSF model, the common keywords are:
    - lambdas: the wavelength [nm]
    - Dx: the distance along X axis to order 0 position of the PSF model centroid  [pixels]
    - Dy: the distance along Y axis to order 0 position of the PSF model centroid [pixels]
    - Dy_mean: the distance along Y axis to order 0 position of the mean dispersion axis [pixels]
    - flux_sum: the transverse sum of the data flux [spectrogram units]
    - flux_integral: the integral of the best fitting PSF model to the data (should be equal to the amplitude parameter
    of the PSF model if the model is correclty normalized to one) [spectrogram units]
    - flux_err: the uncertainty on flux_sum [spectrogram units]
    - fwhm: the FWHM of the best fitting PSF model [pixels]
    - Dy_fwhm_sup: the distance along Y axis to order 0 position of the upper FWHM edge [pixels]
    - Dy_fwhm_inf: the distance along Y axis to order 0 position of the lower FWHM edge [pixels]
    - Dx_rot: the distance along X axis to order 0 position in the rotated spectrogram (no angle) [pixels]

    Then all the specific parameter of the PSF model are stored in other columns with their wavelength evolution
    (read from PSF.param_names attribute).

    A saturation level should be specified in data units.

    """

    def __init__(self, psf, Nx, Ny, deg=4, saturation=None, file_name=''):
        self.my_logger = set_logger(self.__class__.__name__)
        self.psf = psf
        self.deg = -1
        self.degrees = {}
        self.set_polynomial_degrees(deg)
        self.Nx = Nx
        self.Ny = Ny
        self.profile_params = np.zeros((Nx, len(self.psf.param_names)))
        self.pixels = np.mgrid[:Nx, :Ny]
        if file_name == '':
            arr = np.zeros((Nx, len(self.psf.param_names) + 11))
            self.table = Table(arr, names=['lambdas', 'Dx', 'Dy', 'Dy_mean', 'flux_sum', 'flux_integral', 'flux_err',
                                           'fwhm', 'Dy_fwhm_sup', 'Dy_fwhm_inf', 'Dx_rot'] + list(self.psf.param_names))
        else:
            self.table = Table.read(file_name)
        self.psf_param_start_index = 11
        self.n_poly_params = len(self.table)
        self.fitted_pixels = np.arange(len(self.table)).astype(int)
        self.saturation = saturation
        if saturation is None:
            self.saturation = 1e20
            self.my_logger.warning(f"\n\tSaturation level should be given to instanciate the ChromaticPSF "
                                   f"object. self.saturation is set arbitrarily to 1e20. Good luck.")
        for name in self.psf.param_names:
            self.n_poly_params += self.degrees[name] + 1
        self.poly_params = np.zeros(self.n_poly_params)
        self.poly_params_labels = []  # [f"a{k}" for k in range(self.poly_params.size)]
        self.poly_params_names = []  # "$a_{" + str(k) + "}$" for k in range(self.poly_params.size)]
        for ip, p in enumerate(self.psf.param_names):
            if ip == 0:
                self.poly_params_labels += [f"{p}_{k}" for k in range(len(self.table))]
                self.poly_params_names += \
                    ["$" + self.psf.axis_names[ip] + "_{(" + str(k) + ")}$" for k in range(len(self.table))]
            else:
                for k in range(self.degrees[p] + 1):
                    self.poly_params_labels.append(f"{p}_{k}")
                    self.poly_params_names.append("$" + self.psf.axis_names[ip] + "_{(" + str(k) + ")}$")

    def set_polynomial_degrees(self, deg):
        self.deg = deg
        self.degrees = {key: deg for key in self.psf.param_names}
        self.degrees['saturation'] = 0

    def fill_table_with_profile_params(self, profile_params):
        """
        Fill the table with the profile parameters.

        Parameters
        ----------
        profile_params: array
           a Nx * len(self.psf.param_names) numpy array containing the PSF parameters as a function of pixels.

        Examples
        --------

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=8000)
        >>> poly_params_test = s.generate_test_poly_params()
        >>> profile_params = s.from_poly_params_to_profile_params(poly_params_test)
        >>> s.fill_table_with_profile_params(profile_params)

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(s.table['stddev'], 2*np.ones(100))))

        """
        for k, name in enumerate(self.psf.param_names):
            self.table[name] = profile_params[:, k]

    def rotate_table(self, angle_degree):
        """
        In self.table, rotate the columns Dx, Dy, Dy_fwhm_inf and Dy_fwhm_sup by an angle
        given in degree. The results overwrite the previous columns in self.table.

        Parameters
        ----------
        angle_degree: float
            Rotation angle in degree

        Examples
        --------

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=8000)
        >>> s.table['Dx_rot'] = np.arange(100)
        >>> s.rotate_table(45)

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(s.table['Dy'], -np.arange(100)/np.sqrt(2))))
            >>> assert(np.all(np.isclose(s.table['Dx'], np.arange(100)/np.sqrt(2))))
            >>> assert(np.all(np.isclose(s.table['Dy_fwhm_inf'], -np.arange(100)/np.sqrt(2))))
            >>> assert(np.all(np.isclose(s.table['Dy_fwhm_sup'], -np.arange(100)/np.sqrt(2))))
        """
        angle = angle_degree * np.pi / 180.
        rotmat = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
        # finish with Dy_mean to get correct Dx
        for name in ['Dy', 'Dy_fwhm_inf', 'Dy_fwhm_sup', 'Dy_mean']:
            vec = list(np.array([self.table['Dx_rot'], self.table[name]]).T)
            rot_vec = np.array([np.dot(rotmat, v) for v in vec])
            self.table[name] = rot_vec.T[1]
        self.table['Dx'] = rot_vec.T[0]

    def from_profile_params_to_poly_params(self, profile_params):
        """
        Transform the profile_params array into a set of parameters for the chromatic PSF parameterisation.
        Fit Legendre polynomial functions across the pixels for each PSF parameters.
        The order of the polynomial functions is given by the self.degrees array.

        Parameters
        ----------
        profile_params: array
            a Nx * len(self.psf.param_names) numpy array containing the PSF parameters as a function of pixels.

        Returns
        -------
        profile_params: array_like
            A set of parameters that can be evaluated by the chromatic PSF class evaluate function.

        Examples
        --------

        Build a mock spectrogram with random Poisson noise:

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=8000)
        >>> poly_params_test = s.generate_test_poly_params()
        >>> data = s.evaluate(poly_params_test)
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        From the polynomial parameters to the profile parameters:

        >>> profile_params = s.from_poly_params_to_profile_params(poly_params_test)

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(profile_params[0], [0, 0, 50, 5, 2, 0, 2, 8e3])))

        From the profile parameters to the polynomial parameters:

        >>> profile_params = s.from_profile_params_to_poly_params(profile_params)

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(profile_params, poly_params_test)))
        """
        pixels = np.linspace(-1, 1, len(self.table))
        poly_params = np.array([])
        amplitude = None
        for k, name in enumerate(self.psf.param_names):
            if name is 'amplitude':
                amplitude = profile_params[:, k]
                poly_params = np.concatenate([poly_params, amplitude])
        if amplitude is None:
            self.my_logger.warning('\n\tAmplitude array not initialized. '
                                   'Polynomial fit for shape parameters will be unweighted.')
        for k, name in enumerate(self.psf.param_names):
            if name is not 'amplitude':
                weights = np.copy(amplitude)
                # if name is 'stddev':
                #     i_eta = list(self.psf.param_names).index('eta_gauss')
                #     weights = np.abs(amplitude * profile_params[:, i_eta])
                fit = np.polynomial.legendre.legfit(pixels, profile_params[:, k], deg=self.degrees[name], w=weights)
                poly_params = np.concatenate([poly_params, fit])
        return poly_params

    def from_table_to_profile_params(self):
        """
        Extract the profile parameters from self.table and fill an array of profile parameters.

        Parameters
        ----------

        Returns
        -------
        profile_params: array
            Nx * len(self.psf.param_names) numpy array containing the PSF parameters as a function of pixels.

        Examples
        --------

        >>> from spectractor.extractor.spectrum import Spectrum
        >>> s = Spectrum('./tests/data/reduc_20170530_134_spectrum.fits')
        >>> profile_params = s.chromatic_psf.from_table_to_profile_params()

        ..  doctest::
            :hide:

            >>> assert(profile_params.shape == (s.chromatic_psf.Nx, len(s.chromatic_psf.psf.param_names)))
            >>> assert not np.all(np.isclose(profile_params, np.zeros_like(profile_params)))
        """
        profile_params = np.zeros((len(self.table), len(self.psf.param_names)))
        for k, name in enumerate(self.psf.param_names):
            profile_params[:, k] = self.table[name]
        return profile_params

    def from_table_to_poly_params(self):
        """
        Extract the polynomial parameters from self.table and fill an array with polynomial parameters.

        Parameters
        ----------

        Returns
        -------
        poly_params: array_like
            A set of polynomial parameters that can be evaluated by the chromatic PSF class evaluate function.

        Examples
        --------

        >>> from spectractor.extractor.spectrum import Spectrum
        >>> s = Spectrum('./tests/data/reduc_20170530_134_spectrum.fits')
        >>> poly_params = s.chromatic_psf.from_table_to_poly_params()

        ..  doctest::
            :hide:

            >>> assert(poly_params.size > s.chromatic_psf.Nx)
            >>> assert(len(poly_params.shape)==1)
            >>> assert not np.all(np.isclose(poly_params, np.zeros_like(poly_params)))
        """
        profile_params = self.from_table_to_profile_params()
        poly_params = self.from_profile_params_to_poly_params(profile_params)
        return poly_params

    def from_poly_params_to_profile_params(self, poly_params, apply_bounds=False):
        """
        Evaluate the PSF profile parameters from the polynomial coefficients. If poly_params length is smaller
        than self.Nx, it is assumed that the amplitude  parameters are not included and set to arbitrarily to 1.

        Parameters
        ----------
        poly_params: array_like
            Parameter array of the model, in the form:
                - Nx first parameters are amplitudes for the Moffat transverse profiles
                - next parameters are polynomial coefficients for all the PSF parameters in the same order
                as in PSF definition, except amplitude

        apply_bounds: bool, optional
            Force profile parameters to respect their boundary conditions if they lie outside (default: False)

        Returns
        -------
        profile_params: array
            Nx * len(self.psf.param_names) numpy array containing the PSF parameters as a function of pixels.

        Examples
        --------

        Build a mock spectrogram with random Poisson noise:

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=1, saturation=8000)
        >>> poly_params_test = s.generate_test_poly_params()
        >>> data = s.evaluate(poly_params_test)
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        From the polynomial parameters to the profile parameters:

        >>> profile_params = s.from_poly_params_to_profile_params(poly_params_test, apply_bounds=True)

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(profile_params[0], [0, 0, 50, 5, 2, 0, 2, 8e3])))

        From the profile parameters to the polynomial parameters:

        >>> profile_params = s.from_profile_params_to_poly_params(profile_params)

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(profile_params, poly_params_test)))

        From the polynomial parameters to the profile parameters without Moffat amplitudes:

        >>> profile_params = s.from_poly_params_to_profile_params(poly_params_test[100:])

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(profile_params[0], [1, 0, 50, 5, 2, 0, 2, 8e3])))

        """
        length = len(self.table)
        pixels = np.linspace(-1, 1, length)
        profile_params = np.zeros((length, len(self.psf.param_names)))
        shift = 0
        for k, name in enumerate(self.psf.param_names):
            if name == 'amplitude':
                if len(poly_params) > length:
                    profile_params[:, k] = poly_params[:length]
                else:
                    profile_params[:, k] = np.ones(length)
            else:
                if len(poly_params) > length:
                    profile_params[:, k] = \
                        np.polynomial.legendre.legval(pixels,
                                                      poly_params[
                                                      length + shift:length + shift + self.degrees[name] + 1])
                else:
                    p = poly_params[shift:shift + self.degrees[name] + 1]
                    if len(p) > 0:  # to avoid saturation parameters in case not set
                        profile_params[:, k] = np.polynomial.legendre.legval(pixels, p)
                shift = shift + self.degrees[name] + 1
        if apply_bounds:
            for k, name in enumerate(self.psf.param_names):
                indices = profile_params[:, k] <= self.psf.bounds_hard[k][0]
                if np.any(indices):
                    profile_params[indices, k] = self.psf.bounds_hard[k][0]
                indices = profile_params[:, k] > self.psf.bounds_hard[k][1]
                if np.any(indices):
                    profile_params[indices, k] = self.psf.bounds_hard[k][1]
                # if name == "x_mean":
                #    profile_params[profile_params[:, k] <= 0.1, k] = 1e-1
                #    profile_params[profile_params[:, k] >= self.Ny, k] = self.Ny
                # if name == "alpha":
                #     profile_params[profile_params[:, k] <= 1.1, k] = 1.1
                #     # profile_params[profile_params[:, k] >= 6, k] = 6
                # if name == "gamma":
                #     profile_params[profile_params[:, k] <= 0.1, k] = 1e-1
                # if name == "stddev":
                #     profile_params[profile_params[:, k] <= 0.1, k] = 1e-1
                # if name == "eta_gauss":
                #     profile_params[profile_params[:, k] > 0, k] = 0
                #     profile_params[profile_params[:, k] < -1, k] = -1
        return profile_params

    def from_profile_params_to_shape_params(self, profile_params):
        """
        Compute the PSF integrals and FWHMS given the profile_params array and fill the table.

        Parameters
        ----------
        profile_params: array
         a Nx * len(self.psf.param_names) numpy array containing the PSF parameters as a function of pixels.

        Examples
        --------

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=8000)
        >>> poly_params_test = s.generate_test_poly_params()
        >>> profile_params = s.from_poly_params_to_profile_params(poly_params_test)
        >>> s.from_profile_params_to_shape_params(profile_params)

        ..  doctest::
            :hide:

            >>> assert s.table['fwhm'][-1] > 0

        """
        self.fill_table_with_profile_params(profile_params)
        pixel_x = np.arange(self.Nx).astype(int)
        for x in pixel_x:
            p = profile_params[x, :]
            out = self.psf.evaluate(self.pixels, p=p)
            fwhm = compute_fwhm(self.pixels, out, center=p[2], minimum=0)
            self.table['flux_integral'][x] = p[0]  # if MoffatGauss1D normalized
            self.table['fwhm'][x] = fwhm
            self.table['Dy_mean'][x] = 0

    def set_bounds(self):
        """
        This function returns an array of bounds for iminuit. It is very touchy, change the values with caution !

        Returns
        -------
        bounds: array_like
            2D array containing the pair of bounds for each polynomial parameters.

        """
        bounds = [[], []]
        for k, name in enumerate(self.psf.param_names):
            tmp_bounds = [[-np.inf] * (1 + self.degrees[name]), [np.inf] * (1 + self.degrees[name])]
            if name is "saturation":
                tmp_bounds = [[0], [2 * self.saturation]]
            elif name is "amplitude":
                continue
            bounds[0] += tmp_bounds[0]
            bounds[1] += tmp_bounds[1]
        return np.array(bounds).T

    def set_bounds_for_minuit(self, data=None):
        """
        This function returns an array of bounds for iminuit. It is very touchy, change the values with caution !

        Parameters
        ----------
        data: array_like, optional
            The data array, to set the bounds for the amplitude using its maximum.
            If None is provided, no bounds are provided for the amplitude parameters.

        Returns
        -------
        bounds: array_like
            2D array containing the pair of bounds for each polynomial parameters.

        """
        if self.saturation is None:
            self.saturation = 2 * np.max(data)
        if data is not None:
            Ny, Nx = data.shape
            bounds = [[0.1 * np.max(data[:, x]) for x in range(Nx)], [100.0 * np.max(data[:, x]) for x in range(Nx)]]
        else:
            bounds = [[], []]
        for k, name in enumerate(self.psf.param_names):
            tmp_bounds = [[-np.inf] * (1 + self.degrees[name]), [np.inf] * (1 + self.degrees[name])]
            # if name is "x_mean":
            #      tmp_bounds[0].append(0)
            #      tmp_bounds[1].append(Ny)
            # elif name is "gamma":
            #      tmp_bounds[0].append(0)
            #      tmp_bounds[1].append(None) # Ny/2
            # elif name is "alpha":
            #      tmp_bounds[0].append(1)
            #      tmp_bounds[1].append(None) # 10
            # elif name is "eta_gauss":
            #     tmp_bounds[0].append(-1)
            #     tmp_bounds[1].append(0)
            # elif name is "stddev":
            #     tmp_bounds[0].append(0.1)
            #     tmp_bounds[1].append(Ny / 2)
            if name is "saturation":
                if data is not None:
                    tmp_bounds = [[0.1 * np.max(data)], [2 * self.saturation]]
                else:
                    tmp_bounds = [[0], [2 * self.saturation]]
            elif name is "amplitude":
                continue
            # else:
            #     self.my_logger.error(f'Unknown parameter name {name} in set_bounds_for_minuit.')
            bounds[0] += tmp_bounds[0]
            bounds[1] += tmp_bounds[1]
        return np.array(bounds).T

    def check_bounds(self, poly_params, noise_level=0):
        """
        Evaluate the PSF profile parameters from the polynomial coefficients and check if they are within priors.

        Parameters
        ----------
        poly_params: array_like
            Parameter array of the model, in the form:
            - Nx first parameters are amplitudes for the Moffat transverse profiles
            - next parameters are polynomial coefficients for all the PSF parameters
            in the same order as in PSF definition, except amplitude

        noise_level: float, optional
            Noise level to set minimal boundary for amplitudes (negatively).

        Returns
        -------
        in_bounds: bool
            Return True if all parameters respect the model parameter priors.

        """
        in_bounds = True
        penalty = 0
        outbound_parameter_name = ""
        profile_params = self.from_poly_params_to_profile_params(poly_params)
        for k, name in enumerate(self.psf.param_names):
            p = profile_params[:, k]
            if name == 'amplitude':
                if np.any(p < -noise_level):
                    in_bounds = False
                    penalty += np.abs(np.sum(profile_params[p < -noise_level, k]))  # / np.mean(np.abs(p))
                    outbound_parameter_name += name + ' '
            elif name is "saturation":
                continue
            else:
                if np.any(p > self.psf.bounds_soft[k][1]):
                    penalty += np.sum(profile_params[p > self.psf.bounds_soft[k][1], k] - self.psf.bounds_soft[k][1])
                    if not np.isclose(np.mean(p), 0):
                        penalty /= np.abs(np.mean(p))
                    in_bounds = False
                    outbound_parameter_name += name + ' '
                if np.any(p < self.psf.bounds_soft[k][0]):
                    penalty += np.sum(self.psf.bounds_soft[k][0] - profile_params[p < self.psf.bounds_soft[k][0], k])
                    if not np.isclose(np.mean(p), 0):
                        penalty /= np.abs(np.mean(p))
                    in_bounds = False
                    outbound_parameter_name += name + ' '
            # elif name is "stddev":
            #     if np.any(p < 0) or np.any(p > self.Ny):
            #         in_bounds = False
            #         penalty = 1
            #         break
            # else:
            #    self.my_logger.error(f'Unknown parameter name {name} in set_bounds_for_minuit.')
        penalty *= self.Nx * self.Ny
        return in_bounds, penalty, outbound_parameter_name

    def get_distance_along_dispersion_axis(self, shift_x=0, shift_y=0):
        return np.sqrt((self.table['Dx'] - shift_x) ** 2 + (self.table['Dy_mean'] - shift_y) ** 2)

    def evaluate(self, poly_params):  # pragma: no cover
        """
        Dummy function to simulate a 2D spectrogram of size Nx times Ny.

        Parameters
        ----------
        poly_params: array_like
            Parameter array of the model, in the form:
            - Nx first parameters are amplitudes for the Moffat transverse profiles
            - next parameters are polynomial coefficients for all the PSF parameters in the same order
            as in PSF definition, except amplitude

        Returns
        -------
        output: array
            A 2D array with the model

        Examples
        --------
        >>> s = ChromaticPSF1D(Nx=100, Ny=20, deg=4, saturation=8000)
        >>> poly_params = s.generate_test_poly_params()
        >>> output = s.evaluate(poly_params)

        ..  doctest::
            :hide:

            >>> assert not np.all(np.isclose(output, 0))

        >>> import matplotlib.pyplot as plt
        >>> im = plt.imshow(output, origin='lower')  # doctest: +ELLIPSIS
        >>> plt.colorbar(im)  # doctest: +ELLIPSIS
        <matplotlib.colorbar.Colorbar object at 0x...>
        >>> if parameters.DISPLAY: plt.show()

        """
        output = np.zeros((self.Ny, self.Nx))
        return output

    def plot_summary(self, truth=None):
        fig, ax = plt.subplots(2, 1, sharex='all', figsize=(12, 6))
        PSF_models = []
        PSF_truth = []
        if truth is not None:
            PSF_truth = truth.from_poly_params_to_profile_params(truth.poly_params)
        all_pixels = np.arange(self.profile_params.shape[0])
        for i, name in enumerate(self.psf.param_names):
            fit, cov, model = fit_poly1d(all_pixels, self.profile_params[:, i], order=self.degrees[name])
            PSF_models.append(np.polyval(fit, all_pixels))
        for i, name in enumerate(self.psf.param_names):
            p = ax[0].plot(all_pixels, self.profile_params[:, i], marker='+', linestyle='none')
            ax[0].plot(self.fitted_pixels, self.profile_params[self.fitted_pixels, i], label=name,
                       marker='o', linestyle='none', color=p[0].get_color())
            if i > 0:
                ax[0].plot(all_pixels, PSF_models[i], color=p[0].get_color())
            if truth is not None:
                ax[0].plot(all_pixels, PSF_truth[:, i], color=p[0].get_color(), linestyle='--')
        img = np.zeros((self.Ny, self.Nx)).astype(float)
        pixels = np.mgrid[:self.Nx, :self.Ny]
        for x in all_pixels[::self.Nx // 10]:
            params = [PSF_models[p][x] for p in range(len(self.psf.param_names))]
            params[:3] = [1, x, self.Ny // 2]
            out = self.psf.evaluate(pixels, p=params)
            out /= np.max(out)
            img += out
        ax[1].imshow(img, origin='lower') #, extent=[0, self.Nx,
                                          #        self.Ny//2-parameters.PIXWIDTH_SIGNAL,
                                          #        self.Ny//2+parameters.PIXWIDTH_SIGNAL])
        ax[1].set_xlabel('X [pixels]')
        ax[1].set_ylabel('Y [pixels]')
        ax[0].set_ylabel('PSF parameters')
        ax[0].grid()
        ax[1].grid(color='white', ls='solid')
        ax[1].grid(True)
        ax[0].set_yscale('symlog', linthreshy=10)
        ax[1].legend(title='PSF(x)')
        ax[0].legend()
        fig.tight_layout()
        # fig.subplots_adjust(hspace=0)
        if parameters.DISPLAY:  # pragma: no cover
            plt.show()

    def fit_transverse_PSF1D_profile(self, data, err, w, ws, pixel_step=1, bgd_model_func=None, saturation=None,
                                     live_fit=False, sigma=5):
        """
        Fit the transverse profile of a 2D data image with a PSF profile.
        Loop is done on the x-axis direction.
        An order 1 polynomial function is fitted to subtract the background for each pixel
        with a 3*sigma outlier removal procedure to remove background stars.

        Parameters
        ----------
        data: array
            The 2D array image. The transverse profile is fitted on the y direction
            for all pixels along the x direction.
        err: array
            The uncertainties related to the data array.
        w: int
            Half width of central region where the spectrum is extracted and summed (default: 10)
        ws: list
            up/down region extension where the sky background is estimated with format [int, int] (default: [20,30])
        pixel_step: int, optional
            The step in pixels between the slices to be fitted (default: 1).
            The values for the skipped pixels are interpolated with splines from the fitted parameters.
        bgd_model_func: callable, optional
            A 2D function to model the extracted background (default: None -> null background)
        saturation: float, optional
            The saturation level of the image. Default is set to twice the maximum of the data array and has no effect.
        live_fit: bool, optional
            If True, the transverse profile fit is plotted in live accross the loop (default: False).
        sigma: int
            Sigma for outlier rejection (default: 5).

        Examples
        --------

        Build a mock spectrogram with random Poisson noise:

        >>> s0 = ChromaticPSF1D(Nx=100, Ny=100, saturation=1000)
        >>> params = s0.generate_test_poly_params()
        >>> saturation = params[-1]
        >>> data = s0.evaluate(params)
        >>> bgd = 10*np.ones_like(data)
        >>> xx, yy = np.meshgrid(np.arange(s0.Nx), np.arange(s0.Ny))
        >>> bgd += 1000*np.exp(-((xx-20)**2+(yy-10)**2)/(2*2))
        >>> data += bgd
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        Extract the background:

        >>> bgd_model_func = extract_spectrogram_background_sextractor(data, data_errors, ws=[30,50])

        Fit the transverse profile:

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=saturation)
        >>> s.fit_transverse_PSF1D_profile(data, data_errors, w=20, ws=[30,50], pixel_step=10,
        ... bgd_model_func=bgd_model_func, saturation=saturation, live_fit=False, sigma=5)
        >>> s.plot_summary(truth=s0)

        ..  doctest::
            :hide:

            >>> assert(not np.any(np.isclose(s.table['flux_sum'][3:6], np.zeros(s.Nx)[3:6], rtol=1e-3)))
            >>> assert(np.all(np.isclose(s.table['Dy'][-10:-1], np.zeros(s.Nx)[-10:-1], rtol=1e-2)))

        """
        if saturation is None:
            saturation = 2 * np.max(data)
        Ny, Nx = data.shape
        middle = Ny // 2
        index = np.arange(Ny)
        # Prepare the fit: start with the maximum of the spectrum
        xmax_index = int(np.unravel_index(np.argmax(data[middle - ws[0]:middle + ws[0], :]), data.shape)[1])
        bgd_index = np.concatenate((np.arange(0, middle - ws[0]), np.arange(middle + ws[0], Ny))).astype(int)
        y = data[:, xmax_index]
        # first fit with moffat only to initialize the guess
        # hypothesis that max of spectrum if well describe by a focused PSF
        bgd = data[bgd_index, xmax_index]
        if bgd_model_func is not None:
            signal = y - bgd_model_func(xmax_index, index)[:, 0]
        else:
            signal = y
        # fwhm = compute_fwhm(index, signal, minimum=0)
        # Initialize PSF
        psf = MoffatGauss()
        guess = np.copy(psf.p_default)
        # guess = [2 * np.nanmax(signal), middle, 0.5 * fwhm, 2, 0, 0.1 * fwhm, saturation]
        guess[0] = 2 * np.nanmax(signal)
        guess[1] = xmax_index
        guess[2] = middle
        guess[-1] = saturation
        maxi = np.abs(np.nanmax(y))
        # bounds = [(0.1 * maxi, 10 * maxi), (middle - w, middle + w), (0.1, min(fwhm, Ny // 2)), (0.1, self.alpha_max),
        #           (-1, 0),
        #           (0.1, min(Ny // 2, fwhm)),
        #           (0, 2 * saturation)]
        psf.apply_max_width_to_bounds(max_half_width=Ny // 2)
        bounds = np.copy(psf.bounds_hard)
        bounds[0] = (0.1 * maxi, 10 * maxi)
        bounds[2] = (middle - w, middle + w)
        bounds[-1] = (0, 2 * saturation)
        # moffat_guess = [2 * np.nanmax(signal), middle, 0.5 * fwhm, 2]
        # moffat_bounds = [(0.1 * maxi, 10 * maxi), (middle - w, middle + w), (0.1, min(fwhm, Ny // 2)), (0.1, 10)]
        # fit = fit_moffat1d_outlier_removal(index, signal, sigma=sigma, niter=2,
        #                                    guess=moffat_guess, bounds=np.array(moffat_bounds).T)
        # moffat_guess = [getattr(fit, p).value for p in fit.param_names]
        # signal_width_guess = moffat_guess[2]
        # bounds[2] = (0.1, min(Ny // 2, 5 * signal_width_guess))
        # bounds[5] = (0.1, min(Ny // 2, 5 * signal_width_guess))
        # guess[:4] = moffat_guess
        init_guess = np.copy(guess)
        # Go from max to right, then from max to left
        # includes the boundaries to avoid Runge phenomenum in chromatic_fit
        pixel_range = list(np.arange(xmax_index, Nx, pixel_step).astype(int))
        if Nx - 1 not in pixel_range:
            pixel_range.append(Nx - 1)
        pixel_range += list(np.arange(xmax_index, -1, -pixel_step).astype(int))
        if 0 not in pixel_range:
            pixel_range.append(0)
        pixel_range = np.array(pixel_range)
        for x in pixel_range:
            guess = np.copy(guess)
            if x == xmax_index:
                guess = np.copy(init_guess)
            # fit the background with a polynomial function
            y = data[:, x]
            if bgd_model_func is not None:
                # x_array = [x] * index.size
                signal = y - bgd_model_func(x, index)[:, 0]
            else:
                signal = y
            # in case guess amplitude is too low
            # pdf = np.abs(signal)
            # signal_sum = np.nansum(np.abs(signal))
            # if signal_sum > 0:
            #     pdf /= signal_sum
            # mean = np.nansum(pdf * index)
            # bounds[0] = (0.1 * np.nanstd(bgd), 2 * np.nanmax(y[middle - ws[0]:middle + ws[0]]))
            bounds[0] = (0.1 * np.nanstd(bgd), 1.5 * np.nansum(y[middle - ws[0]:middle + ws[0]]))
            # if guess[4] > -1:
            #    guess[0] = np.max(signal) / (1 + guess[4])
            # std = np.sqrt(np.nansum(pdf * (index - mean) ** 2))
            # maxi = np.abs(np.nanmax(signal))
            # if guess[0] * (1 + 0*guess[4]) < 3 * np.nanstd(bgd):
            #     guess[0] = 0.9 * maxi
            # if guess[0] * (1 + 0*guess[4]) > 1.2 * maxi:
            #     guess[0] = 0.9 * maxi
            guess[0] = np.nansum(signal)
            guess[1] = x
            psf_guess = MoffatGauss(p=guess)
            w = PSFFitWorkspace(psf_guess, signal, data_errors=err[:, x], bgd_model_func=None,
                                live_fit=False, verbose=False)
            run_minimisation_sigma_clipping(w, method="minuit", sigma_clip=sigma, niter_clip=2, verbose=False, fix=w.fixed)
            best_fit = w.psf.p
            # It is better not to propagate the guess to further pixel columns
            # otherwise fit_chromatic_psf1D is more likely to get trapped in a local minimum
            # Randomness of the slice fit is better :
            # guess = best_fit
            self.profile_params[x, :] = best_fit
            self.table['flux_err'][x] = np.sqrt(np.sum(err[:, x] ** 2))
            self.table['flux_sum'][x] = np.sum(signal)
            if live_fit and parameters.DISPLAY:  # pragma: no cover
                w.plot_fit()
        # interpolate the skipped pixels with splines
        x = np.arange(Nx)
        xp = np.array(sorted(set(list(pixel_range))))
        self.fitted_pixels = xp
        for i in range(len(self.psf.param_names)):
            yp = self.profile_params[xp, i]
            self.profile_params[:, i] = interp1d(xp, yp, kind='cubic')(x)
        self.table['flux_sum'] = interp1d(xp, self.table['flux_sum'][xp], kind='cubic', bounds_error=False,
                                          fill_value='extrapolate')(x)
        self.table['flux_err'] = interp1d(xp, self.table['flux_err'][xp], kind='cubic', bounds_error=False,
                                          fill_value='extrapolate')(x)
        self.poly_params = self.from_profile_params_to_poly_params(self.profile_params)
        self.from_profile_params_to_shape_params(self.profile_params)

    def fit_chromatic_psf(self, w, data, bgd_model_func=None, data_errors=None):
        """
        Fit a chromatic PSF model on 2D data.

        Parameters
        ----------
        w: ChromaticPSFFitWorkspace
            The ChromaticPSFFitWorkspace.
        data: array_like
            2D array containing the image data.
        bgd_model_func: callable, optional
            A 2D function to model the extracted background (default: None -> null background)
        data_errors: np.array
            the 2D array uncertainties.

        Examples
        --------

        Set the parameters:

        >>> parameters.PIXDIST_BACKGROUND = 40
        >>> parameters.PIXWIDTH_BACKGROUND = 10
        >>> parameters.PIXWIDTH_SIGNAL = 30

        Build a mock spectrogram with random Poisson noise:

        >>> s0 = ChromaticPSF1D(Nx=120, Ny=100, deg=4, saturation=1000)
        >>> params = s0.generate_test_poly_params()
        >>> s0.poly_params = params
        >>> saturation = params[-1]
        >>> data = s0.evaluate(params)
        >>> bgd = 10*np.ones_like(data)
        >>> data += bgd
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        Extract the background:

        >>> bgd_model_func = extract_spectrogram_background_sextractor(data, data_errors, ws=[30,50])

        Estimate the first guess values:

        >>> s = ChromaticPSF1D(Nx=120, Ny=100, deg=4, saturation=saturation)
        >>> s.fit_transverse_PSF1D_profile(data, data_errors, w=20, ws=[30,50],
        ... pixel_step=1, bgd_model_func=bgd_model_func, saturation=saturation, live_fit=False)
        >>> guess = np.copy(s.poly_params)
        >>> s.plot_summary(truth=s0)

        Fit the data:

        >>> w = ChromaticPSF1DFitWorkspace(s, data, data_errors, bgd_model_func=bgd_model_func)
        >>> s.fit_chromatic_psf(w, data, bgd_model_func=bgd_model_func, data_errors=data_errors)
        >>> s.plot_summary(truth=s0)

        ..  doctest::
            :hide:

            >>> residuals = (w.data-w.model)/w.err
            >>> assert w.costs[-1] /(w.Nx*w.Ny) < 1.1
            >>> assert np.abs(np.mean(residuals)) < 0.1
            >>> assert np.std(residuals) < 1.2
        """
        guess = np.copy(self.poly_params)
        run_minimisation(w, method="newton", ftol=1 / (w.Nx * w.Ny), xtol=1e-6, niter=50, fix=w.fixed)
        self.poly_params = w.poly_params

        # add background crop to y_mean
        self.poly_params[w.Nx + w.y_mean_0_index] += w.bgd_width

        # fill results
        self.psf.apply_max_width_to_bounds(max_half_width=w.Ny // 2 + w.bgd_width)
        self.set_bounds()
        self.profile_params = self.from_poly_params_to_profile_params(self.poly_params, apply_bounds=True)
        self.profile_params[:self.Nx, 0] = w.amplitude_params
        self.profile_params[:self.Nx, 1] = np.arange(self.Nx)
        self.fill_table_with_profile_params(self.profile_params)
        self.from_profile_params_to_shape_params(self.profile_params)
        if parameters.DEBUG or True:
            # Plot data, best fit model and residuals:
            self.plot_summary()
            w.plot_fit()


class ChromaticPSF1D(ChromaticPSF):

    def __init__(self, Nx, Ny, deg=4, saturation=None, file_name=''):
        psf = MoffatGauss()
        ChromaticPSF.__init__(self, psf, Nx=Nx, Ny=Ny, deg=deg, saturation=saturation, file_name=file_name)
        self.my_logger = set_logger(self.__class__.__name__)
        self.pixels = np.arange(self.Ny)

    def generate_test_poly_params(self):
        """
        A set of parameters to define a test spectrogram

        Returns
        -------
        profile_params: array
            The list of the test parameters

        Examples
        --------
        >>> s = ChromaticPSF1D(Nx=5, Ny=4, deg=1, saturation=8000)
        >>> params = s.generate_test_poly_params()

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(params,[0, 50, 100, 150, 200, 0, 0, 2, 0, 5, 0, 2, 0, -0.4, -0.4, 2, 0,8000])))

        """
        params = [50 * i for i in range(self.Nx)]
        params += [0.] * (self.degrees['x_mean'] - 1) + [0, 0]  # x mean
        params += [0.] * (self.degrees['y_mean'] - 1) + [0, self.Ny / 2]  # y mean
        params += [0.] * (self.degrees['gamma'] - 1) + [0, 5]  # gamma
        params += [0.] * (self.degrees['alpha'] - 1) + [0, 2]  # alpha
        params += [0.] * (self.degrees['eta_gauss'] - 1) + [-0.4, -0.4]  # eta_gauss
        params += [0.] * (self.degrees['stddev'] - 1) + [0, 2]  # stddev
        params += [8000.]  # saturation
        poly_params = np.zeros_like(params)
        poly_params[:self.Nx] = params[:self.Nx]
        index = self.Nx - 1
        self.saturation = 8000.
        for name in self.psf.param_names:
            if name == 'amplitude':
                continue
            else:
                shift = self.degrees[name] + 1
                c = np.polynomial.legendre.poly2leg(params[index + shift:index:-1])
                coeffs = np.zeros(shift)
                coeffs[:c.size] = c
                poly_params[index + 1:index + shift + 1] = coeffs
                index = index + shift
        return poly_params

    def evaluate(self, poly_params, pixels=None):
        """
        Simulate a 2D spectrogram of size Nx times Ny with transverse 1D PSF profiles.

        Parameters
        ----------
        poly_params: array_like
            Parameter array of the model, in the form:
            - Nx first parameters are amplitudes for the Moffat transverse profiles
            - next parameters are polynomial coefficients for all the PSF parameters
            in the same order as in PSF definition, except amplitude
        pixels: array_like, optional
            The pixel array to evaluate the model (default: None).

        Returns
        -------
        output: array
            A 2D array with the model

        Examples
        --------
        >>> s = ChromaticPSF1D(Nx=100, Ny=20, deg=4, saturation=8000)
        >>> poly_params = s.generate_test_poly_params()
        >>> output = s.evaluate(poly_params)

        >>> import matplotlib.pyplot as plt
        >>> im = plt.imshow(output, origin='lower')  # doctest: +ELLIPSIS
        >>> plt.colorbar(im)  # doctest: +ELLIPSIS
        <matplotlib.colorbar.Colorbar object at 0x...>
        >>> if parameters.DISPLAY: plt.show()

        """
        if pixels is None:
            Ny, Nx = self.Ny, self.Nx
        else:
            Ny, Nx = pixels.size, self.Nx
        self.psf.apply_max_width_to_bounds(max_half_width=Ny // 2)
        profile_params = self.from_poly_params_to_profile_params(poly_params, apply_bounds=True)
        output = np.zeros((Ny, Nx))
        y = np.arange(Ny)
        for k in range(Nx):
            output[:, k] = self.psf.evaluate(y, p=profile_params[k])
        return output

    def fit_chromatic_PSF1D(self, data, bgd_model_func=None, data_errors=None, amplitude_priors_method="noprior"):
        """
        Fit a chromatic PSF model on 2D data.

        Parameters
        ----------
        data: array_like
            2D array containing the image data.
        bgd_model_func: callable, optional
            A 2D function to model the extracted background (default: None -> null background)
        data_errors: np.array, optional
            The 2D array of uncertainties.
        amplitude_priors_method: str, optional
            Prior method to use to constrain the amplitude parameters of the PSF (default: "noprior").

        Returns
        -------
        fit_workspace: ChromaticPSFFitWorkspace
            The ChromaticPSFFitWorkspace instance to get info about the fitting.

        Examples
        --------

        Set the parameters:

        >>> parameters.PIXDIST_BACKGROUND = 40
        >>> parameters.PIXWIDTH_BACKGROUND = 10
        >>> parameters.PIXWIDTH_SIGNAL = 30

        Build a mock spectrogram with random Poisson noise:

        >>> s0 = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=1000)
        >>> params = s0.generate_test_poly_params()
        >>> s0.poly_params = params
        >>> saturation = params[-1]
        >>> data = s0.evaluate(params)
        >>> bgd = 10*np.ones_like(data)
        >>> data += bgd
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        Extract the background:

        >>> bgd_model_func = extract_spectrogram_background_sextractor(data, data_errors, ws=[30,50])

        Estimate the first guess values:

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=saturation)
        >>> s.fit_transverse_PSF1D_profile(data, data_errors, w=20, ws=[30,50],
        ... pixel_step=1, bgd_model_func=bgd_model_func, saturation=saturation, live_fit=False)
        >>> guess = np.copy(s.poly_params)
        >>> s.plot_summary(truth=s0)

        Fit the data:

        >>> w = s.fit_chromatic_PSF1D(data, bgd_model_func=bgd_model_func, data_errors=data_errors,
        ... amplitude_priors_method="noprior")
        >>> s.plot_summary(truth=s0)
        >>> w.plot_fit()

        ..  doctest::
            :hide:

            >>> residuals = (w.data-w.model)/w.err
            >>> assert w.costs[-1] /(w.Nx*w.Ny) < 1.1
            >>> assert np.abs(np.mean(residuals)) < 0.1
            >>> assert np.std(residuals) < 1.2
        """
        w = ChromaticPSF1DFitWorkspace(self, data, data_errors, bgd_model_func=bgd_model_func, live_fit=False,
                                       amplitude_priors_method=amplitude_priors_method)
        self.fit_chromatic_psf(w, data, data_errors=data_errors, bgd_model_func=bgd_model_func)
        return w


class ChromaticPSFFitWorkspace(FitWorkspace):

    def __init__(self, chromatic_psf, data, data_errors, bgd_model_func=None, file_name="",
                 amplitude_priors_method="noprior",
                 nwalkers=18, nsteps=1000, burnin=100, nbins=10,
                 verbose=0, plot=False, live_fit=False, truth=None):
        FitWorkspace.__init__(self, file_name, nwalkers, nsteps, burnin, nbins, verbose, plot,
                              live_fit, truth=truth)
        self.my_logger = set_logger(self.__class__.__name__)
        self.chromatic_psf = chromatic_psf
        self.data = data
        self.err = data_errors
        self.bgd_model_func = bgd_model_func
        length = len(self.chromatic_psf.table)
        self.p = np.copy(self.chromatic_psf.poly_params[length:])  # remove saturation (fixed parameter))
        self.poly_params = np.copy(self.chromatic_psf.poly_params)
        self.input_labels = list(np.copy(self.chromatic_psf.poly_params_labels[length:]))
        self.axis_names = list(np.copy(self.chromatic_psf.poly_params_names[length:]))
        self.fixed = [False] * self.p.size
        for k, par in enumerate(self.input_labels):
            if "x_mean" in par or "saturation" in par:
                self.fixed[k] = True
        self.y_mean_0_index = -1
        for k, par in enumerate(self.input_labels):
            if par == "y_mean_0":
                self.y_mean_0_index = k
                break
        self.nwalkers = max(2 * self.ndim, nwalkers)

        # prepare the fit
        self.Ny, self.Nx = self.data.shape
        if self.Ny != self.chromatic_psf.Ny:
            self.my_logger.error(
                f"\n\tData y shape {self.Ny} different from ChromaticPSF input Ny {self.chromatic_psf.Ny}.")
        if self.Nx != self.chromatic_psf.Nx:
            self.my_logger.error(
                f"\n\tData x shape {self.Nx} different from ChromaticPSF input Nx {self.chromatic_psf.Nx}.")
        self.pixels = np.arange(self.Ny)

        # prepare the background, data and errors
        self.bgd = np.zeros_like(self.data)
        if self.bgd_model_func is not None:
            # xx, yy = np.meshgrid(np.arange(Nx), pixels)
            self.bgd = self.bgd_model_func(np.arange(self.Nx), self.pixels)
        self.data = self.data - self.bgd
        self.bgd_std = float(np.std(np.random.poisson(self.bgd)))

        # crop spectrogram to fit faster
        self.bgd_width = parameters.PIXWIDTH_BACKGROUND + parameters.PIXDIST_BACKGROUND - parameters.PIXWIDTH_SIGNAL
        self.data = self.data[self.bgd_width:-self.bgd_width, :]
        self.pixels = np.arange(self.data.shape[0])
        self.err = np.copy(self.err[self.bgd_width:-self.bgd_width, :])
        self.Ny, self.Nx = self.data.shape

        # update the bounds
        self.chromatic_psf.psf.apply_max_width_to_bounds(max_half_width=self.Ny // 2)
        self.bounds = self.chromatic_psf.set_bounds()

        # error matrix
        self.W = 1. / (self.err * self.err)
        self.W = self.W.flatten()
        self.W_dot_data = np.diag(self.W) @ self.data.flatten()

        # prepare results
        self.amplitude_params = np.zeros(self.Nx)
        self.amplitude_params_err = np.zeros(self.Nx)
        self.cov_matrix = np.zeros((self.Nx, self.Nx))

        # priors on amplitude parameters
        self.amplitude_priors_list = ['noprior', 'positive', 'smooth', 'psf1d', 'fixed']
        self.amplitude_priors_method = amplitude_priors_method
        if amplitude_priors_method not in self.amplitude_priors_list:
            self.my_logger.error(f"\n\tUnknown prior method for the amplitude fitting: {self.amplitude_priors_method}. "
                                 f"Must be either {self.amplitude_priors_list}.")
        if self.amplitude_priors_method == "psf1d":
            self.amplitude_priors = np.copy(self.chromatic_psf.poly_params[:self.Nx])
            # self.amplitude_priors_err = np.copy(self.chromatic_psf.table["flux_err"])
            self.Q = parameters.PSF_FIT_REG_PARAM * np.diag([1 / np.sum(self.err[:, i] ** 2) for i in range(self.Nx)])
            self.Q_dot_A0 = self.Q @ self.amplitude_priors
        if self.amplitude_priors_method == "fixed":
            self.amplitude_priors = np.copy(self.chromatic_psf.poly_params[:self.Nx])

    def plot_fit(self):
        gs_kw = dict(width_ratios=[3, 0.15], height_ratios=[1, 1, 1, 1])
        fig, ax = plt.subplots(nrows=4, ncols=2, figsize=(7, 7), gridspec_kw=gs_kw)
        norm = np.max(self.data)
        plot_image_simple(ax[1, 0], data=self.model / norm, aspect='auto', cax=ax[1, 1], vmin=0, vmax=1,
                          units='1/max(data)')
        ax[1, 0].set_title("Model", fontsize=10, loc='center', color='white', y=0.8)
        plot_image_simple(ax[0, 0], data=self.data / norm, title='Data', aspect='auto',
                          cax=ax[0, 1], vmin=0, vmax=1, units='1/max(data)')
        ax[0, 0].set_title('Data', fontsize=10, loc='center', color='white', y=0.8)
        residuals = (self.data - self.model)
        # residuals_err = self.spectrum.spectrogram_err / self.model
        norm = self.err
        residuals /= norm
        std = float(np.std(residuals))
        plot_image_simple(ax[2, 0], data=residuals, vmin=-5 * std, vmax=5 * std, title='(Data-Model)/Err',
                          aspect='auto', cax=ax[2, 1], units='', cmap="bwr")
        ax[2, 0].set_title('(Data-Model)/Err', fontsize=10, loc='center', color='black', y=0.8)
        ax[2, 0].text(0.05, 0.05, f'mean={np.mean(residuals):.3f}\nstd={np.std(residuals):.3f}',
                      horizontalalignment='left', verticalalignment='bottom',
                      color='black', transform=ax[2, 0].transAxes)
        ax[0, 0].set_xticks(ax[2, 0].get_xticks()[1:-1])
        ax[0, 1].get_yaxis().set_label_coords(3.5, 0.5)
        ax[1, 1].get_yaxis().set_label_coords(3.5, 0.5)
        ax[2, 1].get_yaxis().set_label_coords(3.5, 0.5)
        ax[3, 1].remove()
        ax[3, 0].errorbar(np.arange(self.Nx), self.data.sum(axis=0), yerr=np.sqrt(np.sum(self.err ** 2, axis=0)),
                          label='Data', fmt='k.', markersize=0.1)
        ax[3, 0].plot(np.arange(self.Nx), self.model.sum(axis=0), label='Model')
        ax[3, 0].set_ylabel('Transverse sum')
        ax[3, 0].set_xlabel(r'X [pixels]')
        ax[3, 0].legend(fontsize=7)
        ax[3, 0].set_xlim((0, self.data.shape[1]))
        ax[3, 0].grid(True)
        if self.live_fit:  # pragma: no cover
            plt.draw()
            plt.pause(1e-8)
            plt.close()
        else:
            if parameters.DISPLAY and self.verbose:
                plt.show()
        if parameters.SAVE:  # pragma: no cover
            figname = self.filename.replace(self.filename.split('.')[-1], "_bestfit.pdf")
            self.my_logger.info(f"\n\tSave figure {figname}.")
            fig.savefig(figname, dpi=100, bbox_inches='tight')


class ChromaticPSF1DFitWorkspace(ChromaticPSFFitWorkspace):

    def __init__(self, chromatic_psf, data, data_errors, bgd_model_func=None, file_name="",
                 amplitude_priors_method="noprior",
                 nwalkers=18, nsteps=1000, burnin=100, nbins=10,
                 verbose=0, plot=False, live_fit=False, truth=None):
        ChromaticPSFFitWorkspace.__init__(self, chromatic_psf, data, data_errors, bgd_model_func,
                                          file_name, amplitude_priors_method, nwalkers, nsteps, burnin, nbins, verbose,
                                          plot, live_fit, truth=truth)
        self.my_logger = set_logger(self.__class__.__name__)
        self.pixels = np.arange(self.Ny)

        # error matrix
        self.W = 1. / (self.err * self.err)
        self.W = [np.diag(self.W[:, x]) for x in range(self.Nx)]
        self.W_dot_data = [self.W[x] @ self.data[:, x] for x in range(self.Nx)]

    def simulate(self, *shape_params):
        """
        Compute a ChromaticPSF model given PSF shape parameters and minimizing
        amplitude parameters given a spectrogram data array.

        Parameters
        ----------
        shape_params: array_like
            PSF shape polynomial parameter array.

        Examples
        --------

        Set the parameters:

        >>> parameters.PIXDIST_BACKGROUND = 40
        >>> parameters.PIXWIDTH_BACKGROUND = 10
        >>> parameters.PIXWIDTH_SIGNAL = 30

        Build a mock spectrogram with random Poisson noise:

        >>> s0 = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=1000)
        >>> params = s0.generate_test_poly_params()
        >>> s0.poly_params = params
        >>> saturation = params[-1]
        >>> data = s0.evaluate(params)
        >>> bgd = 10*np.ones_like(data)
        >>> data += bgd
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        Extract the background:

        >>> bgd_model_func = extract_spectrogram_background_sextractor(data, data_errors, ws=[30,50])

        Estimate the first guess values:

        >>> s = ChromaticPSF1D(Nx=100, Ny=100, deg=4, saturation=saturation)
        >>> s.fit_transverse_PSF1D_profile(data, data_errors, w=20, ws=[30,50],
        ... pixel_step=1, bgd_model_func=bgd_model_func, saturation=saturation, live_fit=False)

        Simulate the data:

        >>> w = ChromaticPSF1DFitWorkspace(s, data, data_errors, bgd_model_func=bgd_model_func, verbose=True,
        ... amplitude_priors_method="noprior")
        >>> y, mod, mod_err = w.simulate(*s.poly_params[s.Nx:])
        >>> w.plot_fit()

        ..  doctest::
            :hide:

            >>> assert mod is not None
            >>> assert np.mean(np.abs(mod-w.data)/w.err) < 1

        """
        # linear regression for the amplitude parameters
        poly_params = np.copy(self.poly_params)
        poly_params[self.Nx:] = np.copy(shape_params)
        poly_params[self.Nx + self.y_mean_0_index] -= self.bgd_width
        profile_params = self.chromatic_psf.from_poly_params_to_profile_params(poly_params, apply_bounds=True)
        profile_params[:self.Nx, 0] = 1
        profile_params[:self.Nx, 1] = np.arange(self.Nx)
        # profile_params[:self.Nx, 2] -= self.bgd_width
        if self.amplitude_priors_method != "fixed":
            # Matrix filling
            M = np.array([self.chromatic_psf.psf.evaluate(self.pixels, p=profile_params[x, :]) for x in range(self.Nx)])
            M_dot_W_dot_M = np.array([M[x].T @ self.W[x] @ M[x] for x in range(self.Nx)])
            if self.amplitude_priors_method != "psf1d":
                cov_matrix = np.diag([1 / M_dot_W_dot_M[x] if M_dot_W_dot_M[x] > 0 else 0.1 * self.bgd_std
                                      for x in range(self.Nx)])
                amplitude_params = np.array([
                    M[x].T @ self.W_dot_data[x] / (M_dot_W_dot_M[x]) if M_dot_W_dot_M[x] > 0 else 0.1 * self.bgd_std
                    for x in range(self.Nx)])
                if self.amplitude_priors_method == "positive":
                    amplitude_params[amplitude_params < 0] = 0
                elif self.amplitude_priors_method == "smooth":
                    null_indices = np.where(amplitude_params < 0)[0]
                    for index in null_indices:
                        right = amplitude_params[index]
                        for i in range(index, min(index + 10, self.Nx)):
                            right = amplitude_params[i]
                            if i not in null_indices:
                                break
                        left = amplitude_params[index]
                        for i in range(index, max(0, index - 10), -1):
                            left = amplitude_params[i]
                            if i not in null_indices:
                                break
                        amplitude_params[index] = 0.5 * (right + left)
                elif self.amplitude_priors_method == "noprior":
                    pass
            else:
                M_dot_W_dot_M_plus_Q = [M_dot_W_dot_M[x] + self.Q[x, x] for x in range(self.Nx)]
                cov_matrix = np.diag([1 / M_dot_W_dot_M_plus_Q[x] if M_dot_W_dot_M_plus_Q[x] > 0 else 0.1 * self.bgd_std
                                      for x in range(self.Nx)])
                amplitude_params = [cov_matrix[x, x] * (M[x].T @ self.W_dot_data[x] + self.Q_dot_A0[x])
                                    for x in range(self.Nx)]
        else:
            amplitude_params = np.copy(self.amplitude_priors)
            err2 = np.copy(amplitude_params)
            err2[err2 <= 0] = np.min(np.abs(err2[err2 > 0]))
            cov_matrix = np.diag(err2)
        self.amplitude_params = np.copy(amplitude_params)
        self.amplitude_params_err = np.array([np.sqrt(cov_matrix[x, x])
                                              if cov_matrix[x, x] > 0 else 0 for x in range(self.Nx)])
        self.cov_matrix = np.copy(cov_matrix)
        poly_params[:self.Nx] = amplitude_params
        self.model = self.chromatic_psf.evaluate(poly_params, pixels=self.pixels)  # [self.bgd_width:-self.bgd_width, :]
        self.model_err = np.zeros_like(self.model)
        self.poly_params = np.copy(poly_params)
        return self.pixels, self.model, self.model_err


class ChromaticPSF2D(ChromaticPSF):

    def __init__(self, Nx, Ny, deg=4, saturation=None, file_name=''):
        psf = MoffatGauss()
        ChromaticPSF.__init__(self, psf, Nx=Nx, Ny=Ny, deg=deg, saturation=saturation, file_name=file_name)
        self.my_logger = set_logger(self.__class__.__name__)

    def generate_test_poly_params(self):
        """
        A set of parameters to define a test spectrogram

        Returns
        -------
        profile_params: array
            The list of the test parameters

        Examples
        --------
        >>> s = ChromaticPSF2D(Nx=5, Ny=4, deg=1, saturation=20000)
        >>> params = s.generate_test_poly_params()

        ..  doctest::
            :hide:

            >>> assert(np.all(np.isclose(params,[0, 50, 100, 150, 200, 0, 1, 2, 0, 2, 0, 2, 0, -0.4, -0.4, 1,0,20000])))

        """
        params = [50 * i for i in range(self.Nx)]
        if self.Nx > 80:
            params = list(np.array(params)
                          - 3000 * np.exp(-((np.arange(self.Nx) - 70) / 2) ** 2)
                          - 2000 * np.exp(-((np.arange(self.Nx) - 50) / 2) ** 2))
        params += [0.] * (self.degrees['x_mean'] - 1) + [1, 0]  # x mean
        params += [0.] * (self.degrees['y_mean'] - 1) + [0, self.Ny / 2]  # y mean
        params += [0.] * (self.degrees['gamma'] - 1) + [0, 2]  # gamma
        params += [0.] * (self.degrees['alpha'] - 1) + [0, 2]  # alpha
        params += [0.] * (self.degrees['eta_gauss'] - 1) + [-0.4, -0.4]  # eta_gauss
        params += [0.] * (self.degrees['stddev'] - 1) + [0, 1]  # stddev
        params += [self.saturation]  # saturation
        poly_params = np.zeros_like(params)
        poly_params[:self.Nx] = params[:self.Nx]
        index = self.Nx - 1
        for name in self.psf.param_names:
            if name == 'amplitude':
                continue
            else:
                shift = self.degrees[name] + 1
                c = np.polynomial.legendre.poly2leg(params[index + shift:index:-1])
                coeffs = np.zeros(shift)
                coeffs[:c.size] = c
                poly_params[index + 1:index + shift + 1] = coeffs
                index = index + shift
        return poly_params

    def evaluate(self, poly_params, pixels=None):
        """
        Simulate a 2D spectrogram of size Nx times Ny.

        Parameters
        ----------
        poly_params: array_like
            Parameter array of the model, in the form:
            - Nx first parameters are amplitudes for the Moffat transverse profiles
            - next parameters are polynomial coefficients for all the PSF parameters in the same order
            as in PSF definition, except amplitude
        pixels: array_like, optional
            The pixel array to evaluate the model (default: None).

        Returns
        -------
        output: array
            A 2D array with the model

        Examples
        --------
        >>> s = ChromaticPSF2D(Nx=100, Ny=20, deg=4, saturation=20000)
        >>> poly_params = s.generate_test_poly_params()
        >>> output = s.evaluate(poly_params)

        >>> import matplotlib.pyplot as plt
        >>> im = plt.imshow(output, origin='lower')  # doctest: +ELLIPSIS
        >>> plt.colorbar(im)  # doctest: +ELLIPSIS
        <matplotlib.colorbar.Colorbar object at 0x...>
        >>> if parameters.DISPLAY: plt.show()

        """
        if pixels is None:
            Ny, Nx = self.Ny, self.Nx
            pixels = np.mgrid[:Nx, :Ny]
        else:
            dim, Nx, Ny = pixels.shape
        self.psf.apply_max_width_to_bounds(max_half_width=Ny // 2)
        profile_params = self.from_poly_params_to_profile_params(poly_params, apply_bounds=True)
        # replace x_mean
        profile_params[:, 1] = np.arange(Nx)
        output = np.zeros((Ny, Nx))
        for x in range(Nx):
            output += self.psf.evaluate(pixels, p=profile_params[x, :])
        return output

    def fit_chromatic_PSF2D(self, data, bgd_model_func=None, data_errors=None, amplitude_priors_method="noprior"):
        """
        Fit a chromatic PSF model on 2D data.

        Parameters
        ----------
        data: array_like
            2D array containing the image data.
        bgd_model_func: callable, optional
            A 2D function to model the extracted background (default: None -> null background)
        data_errors: np.array, optional
            The 2D array uncertainties (default: None -> no uncertainties).
        amplitude_priors_method: str, optional
            Prior method to use to constrain the amplitude parameters of the PSF (default: "noprior").

        Returns
        -------
        fit_workspace: ChromaticPSFFitWorkspace
            The ChromaticPSFFitWorkspace instance to get info about the fitting.

        Examples
        --------

        Set the parameters:

        >>> parameters.PIXDIST_BACKGROUND = 40
        >>> parameters.PIXWIDTH_BACKGROUND = 10
        >>> parameters.PIXWIDTH_SIGNAL = 30

        Build a mock spectrogram with random Poisson noise:

        >>> s0 = ChromaticPSF2D(Nx=120, Ny=100, deg=4, saturation=1000)
        >>> params = s0.generate_test_poly_params()
        >>> s0.poly_params = params
        >>> saturation = params[-1]
        >>> data = s0.evaluate(params)
        >>> bgd = 10*np.ones_like(data)
        >>> data += bgd
        >>> data = np.random.poisson(data)
        >>> data_errors = np.sqrt(data+1)

        Extract the background:

        >>> bgd_model_func = extract_spectrogram_background_sextractor(data, data_errors, ws=[30,50])

        Estimate the first guess values:

        >>> s = ChromaticPSF2D(Nx=120, Ny=100, deg=4, saturation=saturation)
        >>> s.fit_transverse_PSF1D_profile(data, data_errors, w=20, ws=[30,50],
        ... pixel_step=1, bgd_model_func=bgd_model_func, saturation=saturation, live_fit=False)
        >>> guess = np.copy(s.poly_params)
        >>> s.plot_summary(truth=s0)

        Fit the data:

        >>> w = s.fit_chromatic_PSF2D(data, bgd_model_func=bgd_model_func, data_errors=data_errors,
        ... amplitude_priors_method="psf1d")
        >>> s.plot_summary(truth=s0)
        >>> w.plot_fit()
        >>> plt.errorbar(s0.poly_params[:s0.Nx], w.amplitude_params-s0.poly_params[:s0.Nx],
        ... yerr=w.amplitude_params_err, fmt="r+") # doctest: +ELLIPSIS
        <ErrorbarContainer ... artists>
        >>> plt.show()

        ..  doctest::
            :hide:

            >>> residuals = (w.data-w.model)/w.err
            >>> assert w.costs[-1] /(w.Nx*w.Ny) < 1.3
            >>> assert np.abs(np.mean(residuals)) < 0.2
            >>> assert np.std(residuals) < 1.2
        """
        # TODO: move amplitude_priors_method to mother class ChromaticPSFFitWorkspace ?
        w = ChromaticPSF2DFitWorkspace(self, data, data_errors, bgd_model_func=bgd_model_func, live_fit=True,
                                       amplitude_priors_method=amplitude_priors_method)
        self.fit_chromatic_psf(w, data, data_errors=data_errors, bgd_model_func=bgd_model_func)
        return w


class ChromaticPSF2DFitWorkspace(ChromaticPSFFitWorkspace):

    def __init__(self, chromatic_psf, data, data_errors, bgd_model_func=None, amplitude_priors_method="noprior",
                 file_name="", nwalkers=18, nsteps=1000, burnin=100, nbins=10,
                 verbose=0, plot=False, live_fit=False, truth=None):
        ChromaticPSFFitWorkspace.__init__(self, chromatic_psf, data, data_errors, bgd_model_func,
                                          file_name, amplitude_priors_method, nwalkers, nsteps, burnin, nbins, verbose,
                                          plot,
                                          live_fit, truth=truth)
        self.my_logger = set_logger(self.__class__.__name__)
        self.pixels = np.mgrid[:self.Nx, :self.Ny]

        # error matrix
        self.W = 1. / (self.err * self.err)
        self.W = self.W.flatten()
        self.W_dot_data = np.diag(self.W) @ self.data.flatten()

    def simulate(self, *shape_params):
        r"""
        Compute a ChromaticPSF2D model given PSF shape parameters and minimizing
        amplitude parameters using a spectrogram data array.

        The ChromaticPSF2D model :math:`\vec{m}(\vec{x},\vec{p})` can be written as

        .. math ::
            :label: chromaticpsf2d

            \vec{m}(\vec{x},\vec{p}) = \sum_{i=0}^{N_x} A_i \phi\left(\vec{x},\vec{p}_i\right)

        with :math:`\vec{x}` the 2D array  of the pixel coordinates, :math:`\vec{A}` the amplitude parameter array
        along the x axis of the spectrogram, :math:`\phi\left(\vec{x},\vec{p}_i\right)` the 2D PSF kernel whose integral
        is normalised to one parametrized with the :math:`\vec{p}_i` non-linear parameter array. If the :math:`\vec{x}`
        2D array is flatten in 1D, equation :eq:`chromaticpsf2d` is

        .. math ::
            :label: chromaticpsf2d_matrix
            :nowrap:

            \begin{align}
            \vec{m}(\vec{x},\vec{p}) & = \mathbf{M}\left(\vec{x},\vec{p}\right) \mathbf{A} \\

            \mathbf{M}\left(\vec{x},\vec{p}\right) & = \left(\begin{array}{cccc}
             \phi\left(\vec{x}_1,\vec{p}_1\right) & \phi\left(\vec{x}_2,\vec{p}_1\right) & ...
             & \phi\left(\vec{x}_{N_x},\vec{p}_1\right) \\
             ... & ... & ... & ...\\
             \phi\left(\vec{x}_1,\vec{p}_{N_x}\right) & \phi\left(\vec{x}_2,\vec{p}_{N_x}\right) & ...
             & \phi\left(\vec{x}_{N_x},\vec{p}_{N_x}\right) \\
            \end{array}\right)
            \end{align}


        with :math:`\mathbf{M}` the design matrix.

        The goal of this function is to perform a minimisation of the amplitude vector :math:`\mathbf{A}` given
        a set of non-linear parameters :math:`\mathbf{p}` and a spectrogram data array :math:`mathbf{y}` modelise as

        .. math:: \mathbf{y} = \mathbf{m}(\vec{x},\vec{p}) + \vec{\epsilon}

        with :math:`\vec{\epsilon}` a random noise vector. The :math:`\chi^2` function to minimise is

        .. math::
            :label: chromaticspsf2d_chi2

            \chi^2(\mathbf{A})= \left(\mathbf{y} - \mathbf{M}\left(\vec{x},\vec{p}\right) \mathbf{A}\right)^T \mathbf{W}
            \left(\mathbf{y} - \mathbf{M}\left(\vec{x},\vec{p}\right) \mathbf{A} \right)


        with :math:`\mathbf{W}` the weight matrix, inverse of the covariance matrix. In our case this matrix is diagonal
        as the pixels are considered all independent. The minimum of equation :eq:`chromaticspsf2d_chi2` is reached for
        a the set of amplitude parameters :math:`\hat{\mathbf{A}}` given by

        .. math::

            \hat{\mathbf{A}} =  (\mathbf{M}^T \mathbf{W} \mathbf{M})^{-1} \mathbf{M}^T \mathbf{W} \mathbf{y}

        The error matrix on the :math:`\hat{\mathbf{A}}` coefficient is simply
        :math:`(\mathbf{M}^T \mathbf{W} \mathbf{M})^{-1}`.


        Parameters
        ----------
        shape_params: array_like
            PSF shape polynomial parameter array.

        Examples
        --------

        Set the parameters:

        .. doctest::

            >>> parameters.PIXDIST_BACKGROUND = 40
            >>> parameters.PIXWIDTH_BACKGROUND = 10
            >>> parameters.PIXWIDTH_SIGNAL = 30

        Build a mock spectrogram with random Poisson noise:

        .. doctest::

            >>> s0 = ChromaticPSF2D(Nx=120, Ny=100, deg=4, saturation=1000)
            >>> params = s0.generate_test_poly_params()
            >>> s0.poly_params = params
            >>> saturation = params[-1]
            >>> data = s0.evaluate(params)
            >>> bgd = 10*np.ones_like(data)
            >>> data += bgd
            >>> data = np.random.poisson(data)
            >>> data_errors = np.sqrt(data+1)

        Extract the background:

        .. doctest::

            >>> bgd_model_func = extract_spectrogram_background_sextractor(data, data_errors, ws=[30,50])

        Estimate the first guess values:

        .. doctest::

            >>> s = ChromaticPSF2D(Nx=120, Ny=100, deg=4, saturation=saturation)
            >>> s.fit_transverse_PSF1D_profile(data, data_errors, w=20, ws=[30,50],
            ... pixel_step=1, bgd_model_func=bgd_model_func, saturation=saturation, live_fit=False)
            >>> s.plot_summary(truth=s0)

        Simulate the data:

        .. doctest::

            >>> w = ChromaticPSF2DFitWorkspace(s, data, data_errors, bgd_model_func=bgd_model_func,
            ... amplitude_priors_method="psf1d", verbose=True)
            >>> y, mod, mod_err = w.simulate(s.poly_params[s.Nx:])
            >>> w.plot_fit()

        .. doctest::
            :hide:

            >>> assert mod is not None


        """
        # linear regression for the amplitude parameters
        # prepare the vectors
        poly_params = np.copy(self.poly_params)
        poly_params[self.Nx:] = np.copy(shape_params)
        poly_params[self.Nx + self.y_mean_0_index] -= self.bgd_width
        profile_params = self.chromatic_psf.from_poly_params_to_profile_params(poly_params, apply_bounds=True)
        profile_params[:self.Nx, 0] = 1
        profile_params[:self.Nx, 1] = np.arange(self.Nx)
        # profile_params[:self.Nx, 2] -= self.bgd_width
        if self.amplitude_priors_method != "fixed":
            # Matrix filling
            W_dot_M = np.zeros((self.Ny * self.Nx, self.Nx))
            M = np.zeros((self.Ny * self.Nx, self.Nx))
            for x in range(self.Nx):
                # self.my_logger.warning(f'\n\t{x} {profile_params[x, :]}')
                M[:, x] = self.chromatic_psf.psf.evaluate(self.pixels, p=profile_params[x, :]).flatten()
                # plt.imshow(self.chromatic_psf.psf.evaluate(self.pixels, p=profile_params[x, :]), origin="lower")
                # plt.imshow(self.data, origin="lower")
                # plt.title(f"{x}")
                # plt.show()
                W_dot_M[:, x] = M[:, x] * self.W
            # Compute the minimizing amplitudes
            M_dot_W_dot_M = M.T @ W_dot_M
            if self.amplitude_priors_method != "psf1d":
                L = np.linalg.inv(np.linalg.cholesky(M_dot_W_dot_M))
                cov_matrix = L.T @ L  # np.linalg.inv(J_dot_W_dot_J)
                amplitude_params = cov_matrix @ (M.T @ self.W_dot_data)
                if self.amplitude_priors_method == "positive":
                    amplitude_params[amplitude_params < 0] = 0
                elif self.amplitude_priors_method == "smooth":
                    null_indices = np.where(amplitude_params < 0)[0]
                    for index in null_indices:
                        right = amplitude_params[index]
                        for i in range(index, min(index + 10, self.Nx)):
                            right = amplitude_params[i]
                            if i not in null_indices:
                                break
                        left = amplitude_params[index]
                        for i in range(index, max(0, index - 10), -1):
                            left = amplitude_params[i]
                            if i not in null_indices:
                                break
                        amplitude_params[index] = 0.5 * (right + left)
                elif self.amplitude_priors_method == "noprior":
                    pass
            else:
                M_dot_W_dot_M_plus_Q = M_dot_W_dot_M + self.Q
                L = np.linalg.inv(np.linalg.cholesky(M_dot_W_dot_M_plus_Q))
                cov_matrix = L.T @ L  # np.linalg.inv(J_dot_W_dot_J)
                amplitude_params = cov_matrix @ (M.T @ self.W_dot_data + self.Q_dot_A0)
        else:
            amplitude_params = np.copy(self.amplitude_priors)
            err2 = np.copy(amplitude_params)
            err2[err2 <= 0] = np.min(np.abs(err2[err2 > 0]))
            cov_matrix = np.diag(err2)
        poly_params[:self.Nx] = amplitude_params
        self.amplitude_params = np.copy(amplitude_params)
        self.amplitude_params_err = np.array([np.sqrt(cov_matrix[i, i]) for i in range(self.Nx)])
        self.cov_matrix = np.copy(cov_matrix)
        # in_bounds, penalty, name = self.chromatic_psf.check_bounds(poly_params, noise_level=self.bgd_std)
        self.model = self.chromatic_psf.evaluate(poly_params, pixels=self.pixels)  # [self.bgd_width:-self.bgd_width, :]
        self.model_err = np.zeros_like(self.model)
        self.poly_params = np.copy(poly_params)
        return self.pixels, self.model, self.model_err


def PSF2D_chisq(params, model, xx, yy, zz, zz_err=None):
    mod = model.evaluate(xx, yy, *params)
    if zz_err is None:
        return np.nansum((mod - zz) ** 2)
    else:
        return np.nansum(((mod - zz) / zz_err) ** 2)


def PSF2D_chisq_jac(params, model, xx, yy, zz, zz_err=None):
    diff = model.evaluate(xx, yy, *params) - zz
    jac = model.fit_deriv(xx, yy, *params)
    if zz_err is None:
        return np.array([np.nansum(2 * jac[p] * diff) for p in range(len(params))])
    else:
        zz_err2 = zz_err * zz_err
        return np.array([np.nansum(2 * jac[p] * diff / zz_err2) for p in range(len(params))])


# DO NOT WORK
# def fit_PSF2D_outlier_removal(x, y, data, sigma=3.0, niter=3, guess=None, bounds=None):
#     """Fit a PSF 2D model with parameters:
#         amplitude_gauss, x_mean, stddev, amplitude, alpha, gamma, saturation
#     using scipy. Find outliers data point above sigma*data_errors from the fit over niter iterations.
#
#     Parameters
#     ----------
#     x: np.array
#         2D array of the x coordinates.
#     y: np.array
#         2D array of the y coordinates.
#     data: np.array
#         the 1D array profile.
#     guess: array_like, optional
#         list containing a first guess for the PSF parameters (default: None).
#     bounds: list, optional
#         2D list containing bounds for the PSF parameters with format ((min,...), (max...)) (default: None)
#     sigma: int
#         the sigma limit to exclude data points (default: 3).
#     niter: int
#         the number of loop iterations to exclude  outliers and refit the model (default: 2).
#
#     Returns
#     -------
#     fitted_model: MoffatGauss2D
#         the MoffatGauss2D fitted model.
#
#     Examples
#     --------
#
#     Create the model:
#     >>> X, Y = np.mgrid[:50,:50]
#     >>> PSF = MoffatGauss2D()
#     >>> p = (1000, 25, 25, 5, 1, -0.2, 1, 6000)
#     >>> Z = PSF.evaluate(X, Y, *p)
#     >>> Z += 100*np.exp(-((X-10)**2+(Y-10)**2)/4)
#     >>> Z_err = np.sqrt(1+Z)
#
#     Prepare the fit:
#     >>> guess = (1000, 27, 23, 3.2, 1.2, -0.1, 2,  6000)
#     >>> bounds = np.array(((0, 6000), (10, 40), (10, 40), (0.5, 10), (0.5, 5), (-1, 0), (0.01, 10), (0, 8000)))
#     >>> bounds = bounds.T
#
#     Fit without bars:
#     >>> model = fit_PSF2D_outlier_removal(X, Y, Z, guess=guess, bounds=bounds, sigma=7, niter=5)
#     >>> res = [getattr(model, p).value for p in model.param_names]
#     >>> print(res, p)
#     >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-1))
#     """
#     gg_init = MoffatGauss2D()
#     if guess is not None:
#         for ip, p in enumerate(gg_init.param_names):
#             getattr(gg_init, p).value = guess[ip]
#     if bounds is not None:
#         for ip, p in enumerate(gg_init.param_names):
#             getattr(gg_init, p).min = bounds[0][ip]
#             getattr(gg_init, p).max = bounds[1][ip]
#     gg_init.saturation.fixed = True
#     with warnings.catch_warnings():
#         # Ignore model linearity warning from the fitter
#         warnings.simplefilter('ignore')
#         fit = LevMarLSQFitterWithNan()
#         or_fit = fitting.FittingWithOutlierRemoval(fit, sigma_clip, niter=niter, sigma=sigma)
#         # get fitted model and filtered data
#         or_fitted_model, filtered_data = or_fit(gg_init, x, y, data)
#         if parameters.VERBOSE:
#             print(or_fitted_model)
#         if parameters.DEBUG:
#             print(fit.fit_info)
#         print(fit.fit_info)
#         return or_fitted_model


def fit_PSF2D(x, y, data, guess=None, bounds=None, data_errors=None, method='minimize'):
    """
    Fit a PSF 2D model with parameters: amplitude, x_mean, y_mean, stddev, eta, alpha, gamma, saturation
    using basin hopping global minimization method.

    Parameters
    ----------
    x: np.array
        2D array of the x coordinates from meshgrid.
    y: np.array
        2D array of the y coordinates from meshgrid.
    data: np.array
        the 2D array image.
    guess: array_like, optional
        List containing a first guess for the PSF parameters (default: None).
    bounds: list, optional
        2D list containing bounds for the PSF parameters with format ((min,...), (max...)) (default: None)
    data_errors: np.array
        the 2D array uncertainties.
    method: str, optional
        the minimisation method: 'minimize' or 'basinhopping' (default: 'minimize').

    Returns
    -------
    fitted_model: PSF2DAstropy
        the PSF fitted model.

    Examples
    --------

    Create the model

    >>> import numpy as np
    >>> X, Y = np.mgrid[:50,:50]
    >>> psf = PSF2DAstropy()
    >>> p = (50, 25, 25, 5, 1, -0.4, 1, 60)
    >>> Z = psf.evaluate(X, Y, *p)
    >>> Z_err = np.sqrt(Z)/10.

    Prepare the fit

    >>> guess = (52, 22, 22, 3.2, 1.2, -0.1, 2, 60)
    >>> bounds = ((1, 200), (10, 40), (10, 40), (0.5, 10), (0.5, 5), (-100, 200), (0.01, 10), (0, 400))

    Fit with error bars

    >>> model = fit_PSF2D(X, Y, Z, guess=guess, bounds=bounds, data_errors=Z_err)
    >>> res = [getattr(model, p).value for p in model.param_names]

    ..  doctest::
        :hide:

        >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))

    Fit without error bars

    >>> model = fit_PSF2D(X, Y, Z, guess=guess, bounds=bounds, data_errors=None)
    >>> res = [getattr(model, p).value for p in model.param_names]

    ..  doctest::
        :hide:

        >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))

    Fit with error bars and basin hopping method

    >>> model = fit_PSF2D(X, Y, Z, guess=guess, bounds=bounds, data_errors=Z_err, method='basinhopping')
    >>> res = [getattr(model, p).value for p in model.param_names]

    ..  doctest::
        :hide:

        >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))

    """

    model = PSF2DAstropy()
    my_logger = set_logger(__name__)
    if method == 'minimize':
        res = minimize(PSF2D_chisq, guess, method="L-BFGS-B", bounds=bounds,
                       args=(model, x, y, data, data_errors), jac=PSF2D_chisq_jac)
    elif method == 'basinhopping':
        minimizer_kwargs = dict(method="L-BFGS-B", bounds=bounds, jac=PSF2D_chisq_jac,
                                args=(model, x, y, data, data_errors))
        res = basinhopping(PSF2D_chisq, guess, niter=20, minimizer_kwargs=minimizer_kwargs)
    else:
        my_logger.error(f'\n\tUnknown method {method}.')
        sys.exit()
    my_logger.debug(f'\n{res}')
    psf = PSF2DAstropy(*res.x)
    my_logger.debug(f'\n\tPSF best fitting parameters:\n{psf}')
    return psf


def fit_PSF2D_minuit(x, y, data, guess=None, bounds=None, data_errors=None):
    """
    Fit a PSF 2D model with parameters: amplitude, x_mean, y_mean, stddev, eta, alpha, gamma, saturation
    using basin hopping global minimization method.

    Parameters
    ----------
    x: np.array
        2D array of the x coordinates from meshgrid.
    y: np.array
        2D array of the y coordinates from meshgrid.
    data: np.array
        the 2D array image.
    guess: array_like, optional
        List containing a first guess for the PSF parameters (default: None).
    bounds: list, optional
        2D list containing bounds for the PSF parameters with format ((min,...), (max...)) (default: None)
    data_errors: np.array
        the 2D array uncertainties.

    Returns
    -------
    fitted_model: PSF2DAstropy
        the PSF2D fitted model.

    Examples
    --------

    Create the model

    >>> import numpy as np
    >>> X, Y = np.mgrid[:50,:50]
    >>> psf = PSF2DAstropy()
    >>> p = (50, 25, 25, 5, 1, -0.4, 1, 60)
    >>> Z = psf.evaluate(X, Y, *p)
    >>> Z_err = np.sqrt(Z)/10.

    Prepare the fit

    >>> guess = (52, 22, 22, 3.2, 1.2, -0.1, 2, 60)
    >>> bounds = ((1, 200), (10, 40), (10, 40), (0.5, 10), (0.5, 5), (-100, 200), (0.01, 10), (0, 400))

    Fit with error bars

    >>> model = fit_PSF2D_minuit(X, Y, Z, guess=guess, bounds=bounds, data_errors=Z_err)
    >>> res = [getattr(model, p).value for p in model.param_names]

    ..  doctest::
        :hide:

        >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))

    Fit without error bars

    >>> model = fit_PSF2D_minuit(X, Y, Z, guess=guess, bounds=bounds, data_errors=None)
    >>> res = [getattr(model, p).value for p in model.param_names]

    ..  doctest::
        :hide:

        >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))
    """

    model = PSF2DAstropy()
    my_logger = set_logger(__name__)

    if bounds is not None:
        bounds = np.array(bounds)
        if bounds.shape[0] == 2 and bounds.shape[1] > 2:
            bounds = bounds.T

    guess = np.array(guess)
    error = 0.001 * np.abs(guess) * np.ones_like(guess)
    z = np.where(np.isclose(error, 0.0, 1e-6))
    error[z] = 0.001

    def chisq_PSF2D(params):
        return PSF2D_chisq(params, model, x, y, data, data_errors)

    def chisq_PSF2D_jac(params):
        return PSF2D_chisq_jac(params, model, x, y, data, data_errors)

    fix = [False] * error.size
    fix[-1] = True
    # noinspection PyArgumentList
    m = Minuit.from_array_func(fcn=chisq_PSF2D, start=guess, error=error, errordef=1,
                               fix=fix, print_level=0, limit=bounds, grad=chisq_PSF2D_jac)

    m.tol = 0.001
    m.migrad()
    popt = m.np_values()

    my_logger.debug(f'\n{popt}')
    psf = PSF2DAstropy(*popt)
    my_logger.debug(f'\n\tPSF best fitting parameters:\n{psf}')
    return psf


@deprecated(reason='Use MoffatGauss1D class instead.')
class PSF1DAstropy(Fittable1DModel):
    n_inputs = 1
    n_outputs = 1
    # inputs = ('x',)
    # outputs = ('y',)

    amplitude_moffat = Parameter('amplitude_moffat', default=0.5)
    x_mean = Parameter('x_mean', default=0)
    gamma = Parameter('gamma', default=3)
    alpha = Parameter('alpha', default=3)
    eta_gauss = Parameter('eta_gauss', default=1)
    stddev = Parameter('stddev', default=1)
    saturation = Parameter('saturation', default=1)

    axis_names = ["A", "y", r"\gamma", r"\alpha", r"\eta", r"\sigma", "saturation"]

    @staticmethod
    def evaluate(x, amplitude_moffat, x_mean, gamma, alpha, eta_gauss, stddev, saturation):
        rr = (x - x_mean) * (x - x_mean)
        rr_gg = rr / (gamma * gamma)
        # use **(-alpha) instead of **(alpha) to avoid overflow power errors due to high alpha exponents
        # import warnings
        # warnings.filterwarnings('error')
        try:
            a = amplitude_moffat * ((1 + rr_gg) ** (-alpha) + eta_gauss * np.exp(-(rr / (2. * stddev * stddev))))
        except RuntimeWarning:  # pragma: no cover
            my_logger = set_logger(__name__)
            my_logger.warning(f"{[amplitude_moffat, x_mean, gamma, alpha, eta_gauss, stddev, saturation]}")
            a = amplitude_moffat * eta_gauss * np.exp(-(rr / (2. * stddev * stddev)))
        return np.clip(a, 0, saturation)

    @staticmethod
    def fit_deriv(x, amplitude_moffat, x_mean, gamma, alpha, eta_gauss, stddev, saturation):
        rr = (x - x_mean) * (x - x_mean)
        rr_gg = rr / (gamma * gamma)
        gauss_norm = np.exp(-(rr / (2. * stddev * stddev)))
        d_eta_gauss = amplitude_moffat * gauss_norm
        moffat_norm = (1 + rr_gg) ** (-alpha)
        d_amplitude_moffat = moffat_norm + eta_gauss * gauss_norm
        d_x_mean = amplitude_moffat * (eta_gauss * (x - x_mean) / (stddev * stddev) * gauss_norm
                                       - alpha * moffat_norm * (-2 * x + 2 * x_mean) / (
                                               gamma * gamma * (1 + rr_gg)))
        d_stddev = amplitude_moffat * eta_gauss * rr / (stddev ** 3) * gauss_norm
        d_alpha = - amplitude_moffat * moffat_norm * np.log(1 + rr_gg)
        d_gamma = 2 * amplitude_moffat * alpha * moffat_norm * (rr_gg / (gamma * (1 + rr_gg)))
        d_saturation = saturation * np.zeros_like(x)
        return np.array([d_amplitude_moffat, d_x_mean, d_gamma, d_alpha, d_eta_gauss, d_stddev, d_saturation])

    @staticmethod
    def deriv(x, amplitude_moffat, x_mean, gamma, alpha, eta_gauss, stddev, saturation):
        rr = (x - x_mean) * (x - x_mean)
        rr_gg = rr / (gamma * gamma)
        d_eta_gauss = np.exp(-(rr / (2. * stddev * stddev)))
        d_gauss = - eta_gauss * (x - x_mean) / (stddev * stddev) * d_eta_gauss
        d_moffat = -  alpha * 2 * (x - x_mean) / (gamma * gamma * (1 + rr_gg) ** (alpha + 1))
        return amplitude_moffat * (d_gauss + d_moffat)

    def interpolation(self, x_array):
        """

        Parameters
        ----------
        x_array: array_like
            The abscisse array to interpolate the model.

        Returns
        -------
        interp: callable
            Function corresponding to the interpolated model on the x_array array.

        Examples
        --------
        >>> x = np.arange(0, 60, 1)
        >>> p = [2,0,2,2,1,2,10]
        >>> psf = PSF1DAstropy(*p)
        >>> interp = psf.interpolation(x)

        ..  doctest::
            :hide:

            >>> assert np.isclose(interp(p[1]), psf.evaluate(p[1], *p))

        """
        params = [getattr(self, p).value for p in self.param_names]
        return interp1d(x_array, self.evaluate(x_array, *params), fill_value=0, bounds_error=False)

    def integrate(self, bounds=(-np.inf, np.inf), x_array=None):
        """
        Compute the integral of the PSF model. Bounds are -np.inf, np.inf by default, or provided
        if no x_array is provided. Otherwise the bounds comes from x_array edges.

        Parameters
        ----------
        x_array: array_like, optional
            If not None, the interpoalted PSF modelis used for integration (default: None).
        bounds: array_like, optional
            The bounds of the integral (default bounds=(-np.inf, np.inf)).

        Returns
        -------
        result: float
            The integral of the PSF model.

        Examples
        --------

        .. doctest::

            >>> x = np.arange(0, 60, 1)
            >>> p = [2,30,4,2,-0.5,1,10]
            >>> psf = PSF1DAstropy(*p)
            >>> xx = np.arange(0, 60, 0.01)
            >>> plt.plot(xx, psf.evaluate(xx, *p)) # doctest: +ELLIPSIS
            [<matplotlib.lines.Line2D object at ...>]
            >>> plt.plot(x, psf.evaluate(x, *p)) # doctest: +ELLIPSIS
            [<matplotlib.lines.Line2D object at ...>]
            >>> if parameters.DISPLAY: plt.show()

        .. plot::

            import matplotlib.pyplot as plt
            import numpy as np
            from spectractor.extractor.psf import PSF1DAstropy
            p = [2,30,4,2,-0.5,1,10]
            x = np.arange(0, 60, 1)
            xx = np.arange(0, 60, 0.01)
            psf = PSF1DAstropy(*p)
            fig = plt.figure(figsize=(5,3))
            plt.plot(xx, psf.evaluate(xx, *p), label="high sampling")
            plt.plot(x, psf.evaluate(x, *p), label="low sampling")
            plt.grid()
            plt.xlabel('x')
            plt.ylabel('PSF(x)')
            plt.legend()
            plt.show()

        .. doctest::

            >>> psf.integrate()  # doctest: +ELLIPSIS
            10.0597...
            >>> psf.integrate(bounds=(0,60), x_array=x)  # doctest: +ELLIPSIS
            10.0466...

        """
        params = [getattr(self, p).value for p in self.param_names]
        if x_array is None:
            i = quad(self.evaluate, bounds[0], bounds[1], args=tuple(params), limit=200)
            return i[0]
        else:
            return np.trapz(self.evaluate(x_array, *params), x_array)

    def fwhm(self, x_array=None):
        """
        Compute the full width half maximum of the PSF model with a dichotomie method.

        Parameters
        ----------
        x_array: array_like, optional
            An abscisse array is one wants to find FWHM on the interpolated PSF model
            (to smooth the spikes from spurious parameter sets).

        Returns
        -------
        FWHM: float
            The full width half maximum of the PSF model.

        Examples
        --------
        >>> x = np.arange(0, 60, 1)
        >>> p = [2,30,4,2,-0.4,1,10]
        >>> psf = PSF1DAstropy(*p)
        >>> a, b =  p[1], p[1]+3*max(p[-2], p[2])
        >>> fwhm = psf.fwhm(x_array=None)
        >>> assert np.isclose(fwhm, 7.25390625)
        >>> fwhm = psf.fwhm(x_array=x)
        >>> assert np.isclose(fwhm, 7.083984375)
        >>> print(fwhm)
        7.083984375
        >>> import matplotlib.pyplot as plt
        >>> x = np.arange(0, 60, 0.01)
        >>> plt.plot(x, psf.evaluate(x, *p)) # doctest: +ELLIPSIS
        [<matplotlib.lines.Line2D object at 0x...>]
        >>> if parameters.DISPLAY: plt.show()
        """
        params = [getattr(self, p).value for p in self.param_names]
        interp = None
        if x_array is not None:
            interp = self.interpolation(x_array)
            values = self.evaluate(x_array, *params)
            maximum = np.max(values)
            imax = np.argmax(values)
            a = imax + np.argmin(np.abs(values[imax:] - 0.95 * maximum))
            b = imax + np.argmin(np.abs(values[imax:] - 0.05 * maximum))

            def eq(x):
                return interp(x) - 0.5 * maximum
        else:
            maximum = self.amplitude_moffat.value * (1 + self.eta_gauss.value)
            a = self.x_mean.value
            b = self.x_mean.value + 3 * max(self.gamma.value, self.stddev.value)

            def eq(x):
                return self.evaluate(x, *params) - 0.5 * maximum
        res = dichotomie(eq, a, b, 1e-2)
        # res = newton()
        return abs(2 * (res - self.x_mean.value))


@deprecated(reason='Use MoffatGauss1D class instead.')
def PSF1D_chisq(params, model, xx, yy, yy_err=None):
    m = model.evaluate(xx, *params)
    if len(m) == 0 or len(yy) == 0:
        return 1e20
    if np.any(m < 0) or np.any(m > 1.5 * np.max(yy)) or np.max(m) < 0.5 * np.max(yy):
        return 1e20
    diff = m - yy
    if yy_err is None:
        return np.nansum(diff * diff)
    else:
        return np.nansum((diff / yy_err) ** 2)


@deprecated(reason='Use MoffatGauss1D class instead.')
def PSF1D_chisq_jac(params, model, xx, yy, yy_err=None):
    diff = model.evaluate(xx, *params) - yy
    jac = model.fit_deriv(xx, *params)
    if yy_err is None:
        return np.array([np.nansum(2 * jac[p] * diff) for p in range(len(params))])
    else:
        yy_err2 = yy_err * yy_err
        return np.array([np.nansum(2 * jac[p] * diff / yy_err2) for p in range(len(params))])


@deprecated(reason='Use MoffatGauss1D class instead.')
def fit_PSF1D(x, data, guess=None, bounds=None, data_errors=None, method='minimize'):
    """Fit a PSF 1D Astropy model with parameters :
        amplitude_gauss, x_mean, stddev, amplitude_moffat, alpha, gamma, saturation

    using basin hopping global minimization method.

    Parameters
    ----------
    x: np.array
        1D array of the x coordinates.
    data: np.array
        the 1D array profile.
    guess: array_like, optional
        list containing a first guess for the PSF parameters (default: None).
    bounds: list, optional
        2D list containing bounds for the PSF parameters with format ((min,...), (max...)) (default: None)
    data_errors: np.array
        the 1D array uncertainties.
    method: str, optional
        method to use for the minimisation: choose between minimize and basinhopping.

    Returns
    -------
    fitted_model: PSF1DAstropy
        the PSF fitted model.

    Examples
    --------

    Create the model:

    >>> import numpy as np
    >>> X = np.arange(0, 50)
    >>> psf = PSF1DAstropy()
    >>> p = (50, 25, 5, 1, -0.2, 1, 60)
    >>> Y = psf.evaluate(X, *p)
    >>> Y_err = np.sqrt(Y)/10.

    Prepare the fit:

    >>> guess = (60, 20, 3.2, 1.2, -0.1, 2,  60)
    >>> bounds = ((0, 200), (10, 40), (0.5, 10), (0.5, 5), (-10, 200), (0.01, 10), (0, 400))

    Fit with error bars:
    # >>> model = fit_PSF1D(X, Y, guess=guess, bounds=bounds, data_errors=Y_err)
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))
    #
    # Fit without error bars:
    # >>> model = fit_PSF1D(X, Y, guess=guess, bounds=bounds, data_errors=None)
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))
    #
    # Fit with error bars and basin hopping method:
    # >>> model = fit_PSF1D(X, Y, guess=guess, bounds=bounds, data_errors=Y_err, method='basinhopping')
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-3))

    """
    my_logger = set_logger(__name__)
    model = PSF1DAstropy()
    if method == 'minimize':
        res = minimize(PSF1D_chisq, guess, method="L-BFGS-B", bounds=bounds,
                       args=(model, x, data, data_errors), jac=PSF1D_chisq_jac)
    elif method == 'basinhopping':
        minimizer_kwargs = dict(method="L-BFGS-B", bounds=bounds,
                                args=(model, x, data, data_errors), jac=PSF1D_chisq_jac)
        res = basinhopping(PSF1D_chisq, guess, niter=20, minimizer_kwargs=minimizer_kwargs)
    else:
        my_logger.error(f'\n\tUnknown method {method}.')
        sys.exit()
    my_logger.debug(f'\n{res}')
    psf = PSF1DAstropy(*res.x)
    my_logger.debug(f'\n\tPSF best fitting parameters:\n{psf}')
    return psf


@deprecated(reason='Use MoffatGauss1D class instead. Mainly because PSF integral must be normalized to one.')
def fit_PSF1D_outlier_removal(x, data, data_errors=None, sigma=3.0, niter=3, guess=None, bounds=None, method='minimize',
                              niter_basinhopping=5, T_basinhopping=0.2):
    """Fit a PSF 1D Astropy model with parameters:
        amplitude_gauss, x_mean, stddev, amplitude_moffat, alpha, gamma, saturation

    using scipy. Find outliers data point above sigma*data_errors from the fit over niter iterations.

    Parameters
    ----------
    x: np.array
        1D array of the x coordinates.
    data: np.array
        the 1D array profile.
    data_errors: np.array
        the 1D array uncertainties.
    guess: array_like, optional
        list containing a first guess for the PSF parameters (default: None).
    bounds: list, optional
        2D list containing bounds for the PSF parameters with format ((min,...), (max...)) (default: None)
    sigma: int
        the sigma limit to exclude data points (default: 3).
    niter: int
        the number of loop iterations to exclude  outliers and refit the model (default: 2).
    method: str
        Can be 'minimize' or 'basinhopping' (default: 'minimize').
    niter_basinhopping: int, optional
        The number of basin hops (default: 5)
    T_basinhopping: float, optional
        The temperature for the basin hops (default: 0.2)

    Returns
    -------
    fitted_model: PSF1DAstropy
        the PSF fitted model.
    outliers: list
        the list of the outlier indices.

    Examples
    --------

    Create the model:

    >>> import numpy as np
    >>> X = np.arange(0, 50)
    >>> psf = PSF1DAstropy()
    >>> p = (1000, 25, 5, 1, -0.2, 1, 6000)
    >>> Y = psf.evaluate(X, *p)
    >>> Y += 100*np.exp(-((X-10)/2)**2)
    >>> Y_err = np.sqrt(1+Y)

    Prepare the fit:

    >>> guess = (600, 27, 3.2, 1.2, -0.1, 2,  6000)
    >>> bounds = ((0, 6000), (10, 40), (0.5, 10), (0.5, 5), (-1, 0), (0.01, 10), (0, 8000))

    Fit without bars:
    # >>> model, outliers = fit_PSF1D_outlier_removal(X, Y, guess=guess, bounds=bounds,
    # ... sigma=3, niter=5, method="minimize")
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-1))
    #
    # Fit with error bars:
    # >>> model, outliers = fit_PSF1D_outlier_removal(X, Y, guess=guess, bounds=bounds, data_errors=Y_err,
    # ... sigma=3, niter=2, method="minimize")
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-1))
    #
    # Fit with error bars and basinhopping:
    # >>> model, outliers = fit_PSF1D_outlier_removal(X, Y, guess=guess, bounds=bounds, data_errors=Y_err,
    # ... sigma=3, niter=5, method="basinhopping", niter_basinhopping=20)
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-1))
    """

    my_logger = set_logger(__name__)
    indices = np.mgrid[:x.shape[0]]
    outliers = np.array([])
    model = PSF1DAstropy()

    for step in range(niter):
        # first fit
        if data_errors is None:
            err = None
        else:
            err = data_errors[indices]
        if method == 'minimize':
            res = minimize(PSF1D_chisq, guess, method="L-BFGS-B", bounds=bounds, jac=PSF1D_chisq_jac,
                           args=(model, x[indices], data[indices], err))
        elif method == 'basinhopping':
            minimizer_kwargs = dict(method="L-BFGS-B", bounds=bounds, jac=PSF1D_chisq_jac,
                                    args=(model, x[indices], data[indices], err))
            res = basinhopping(PSF1D_chisq, guess, T=T_basinhopping, niter=niter_basinhopping,
                               minimizer_kwargs=minimizer_kwargs)
        else:
            my_logger.error(f'\n\tUnknown method {method}.')
            sys.exit()
        if parameters.DEBUG:
            my_logger.debug(f'\n\tniter={step}\n{res}')
        # update the model and the guess
        for ip, p in enumerate(model.param_names):
            setattr(model, p, res.x[ip])
        guess = res.x
        # remove outliers
        indices_no_nan = ~np.isnan(data)
        diff = model(x[indices_no_nan]) - data[indices_no_nan]
        if data_errors is not None:
            outliers = np.where(np.abs(diff) / data_errors[indices_no_nan] > sigma)[0]
        else:
            std = np.std(diff)
            outliers = np.where(np.abs(diff) / std > sigma)[0]
        if len(outliers) > 0:
            indices = [i for i in range(x.shape[0]) if i not in outliers]
        else:
            break
    my_logger.debug(f'\n\tPSF best fitting parameters:\n{model}')
    return model, outliers


@deprecated(reason='Use MoffatGauss1D class instead.')
def fit_PSF1D_minuit(x, data, guess=None, bounds=None, data_errors=None):
    """Fit a PSF 1D Astropy model with parameters:
        amplitude_gauss, x_mean, stddev, amplitude_moffat, alpha, gamma, saturation

    using Minuit.

    Parameters
    ----------
    x: np.array
        1D array of the x coordinates.
    data: np.array
        the 1D array profile.
    guess: array_like, optional
        list containing a first guess for the PSF parameters (default: None).
    bounds: list, optional
        2D list containing bounds for the PSF parameters with format ((min,...), (max...)) (default: None)
    data_errors: np.array
        the 1D array uncertainties.

    Returns
    -------
    fitted_model: PSF1DAstropy
        the PSF fitted model.

    Examples
    --------

    Create the model:

    >>> import numpy as np
    >>> X = np.arange(0, 50)
    >>> psf = PSF1DAstropy()
    >>> p = (50, 25, 5, 1, -0.2, 1, 60)
    >>> Y = psf.evaluate(X, *p)
    >>> Y_err = np.sqrt(1+Y)

    Prepare the fit:

    >>> guess = (60, 20, 3.2, 1.2, -0.1, 2,  60)
    >>> bounds = ((0, 200), (10, 40), (0.5, 10), (0.5, 5), (-1, 0), (0.01, 10), (0, 400))

    Fit with error bars:
    # >>> model = fit_PSF1D_minuit(X, Y, guess=guess, bounds=bounds, data_errors=Y_err)
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-2))
    #
    # Fit without error bars:
    # >>> model = fit_PSF1D_minuit(X, Y, guess=guess, bounds=bounds, data_errors=None)
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-2))

    """

    my_logger = set_logger(__name__)
    model = PSF1DAstropy()

    def PSF1D_chisq_v2(params):
        mod = model.evaluate(x, *params)
        diff = mod - data
        if data_errors is None:
            return np.nansum(diff * diff)
        else:
            return np.nansum((diff / data_errors) ** 2)

    error = 0.1 * np.abs(guess) * np.ones_like(guess)
    fix = [False] * len(guess)
    fix[-1] = True
    # noinspection PyArgumentList
    # 3 times faster with gradient
    m = Minuit.from_array_func(fcn=PSF1D_chisq_v2, start=guess, error=error, errordef=1, limit=bounds, fix=fix,
                               print_level=parameters.DEBUG)
    m.migrad()
    psf = PSF1DAstropy(*m.np_values())

    my_logger.debug(f'\n\tPSF best fitting parameters:\n{psf}')
    return psf


@deprecated(reason='Use MoffatGauss1D class instead.')
def fit_PSF1D_minuit_outlier_removal(x, data, data_errors, guess=None, bounds=None, sigma=3, niter=2, consecutive=3):
    """Fit a PSF Astropy 1D model with parameters:
        amplitude_gauss, x_mean, stddev, amplitude_moffat, alpha, gamma, saturation

    using Minuit. Find outliers data point above sigma*data_errors from the fit over niter iterations.
    Only at least 3 consecutive outliers are considered.

    Parameters
    ----------
    x: np.array
        1D array of the x coordinates.
    data: np.array
        the 1D array profile.
    data_errors: np.array
        the 1D array uncertainties.
    guess: array_like, optional
        list containing a first guess for the PSF parameters (default: None).
    bounds: list, optional
        2D list containing bounds for the PSF parameters with format ((min,...), (max...)) (default: None)
    sigma: int
        the sigma limit to exclude data points (default: 3).
    niter: int
        the number of loop iterations to exclude  outliers and refit the model (default: 2).
    consecutive: int
        the number of outliers that have to be consecutive to be considered (default: 3).

    Returns
    -------
    fitted_model: PSF1DAstropy
        the PSF fitted model.
    outliers: list
        the list of the outlier indices.

    Examples
    --------

    Create the model:

    >>> import numpy as np
    >>> X = np.arange(0, 50)
    >>> psf = PSF1DAstropy()
    >>> p = (1000, 25, 5, 1, -0.2, 1, 6000)
    >>> Y = psf.evaluate(X, *p)
    >>> Y += 100*np.exp(-((X-10)/2)**2)
    >>> Y_err = np.sqrt(1+Y)

    Prepare the fit:

    >>> guess = (600, 20, 3.2, 1.2, -0.1, 2,  6000)
    >>> bounds = ((0, 6000), (10, 40), (0.5, 10), (0.5, 5), (-1, 0), (0.01, 10), (0, 8000))

    Fit with error bars:
    # >>> model, outliers = fit_PSF1D_minuit_outlier_removal(X, Y, guess=guess, bounds=bounds, data_errors=Y_err,
    # ... sigma=3, niter=2, consecutive=3)
    # >>> res = [getattr(model, p).value for p in model.param_names]
    # >>> assert np.all(np.isclose(p[:-1], res[:-1], rtol=1e-1))
    """

    psf = PSF1DAstropy(*guess)
    model = PSF1DAstropy()
    outliers = np.array([])
    indices = [i for i in range(x.shape[0]) if i not in outliers]

    def PSF1D_chisq_v2(params):
        mod = model.evaluate(x, *params)
        diff = mod[indices] - data[indices]
        if data_errors is None:
            return np.nansum(diff * diff)
        else:
            return np.nansum((diff / data_errors[indices]) ** 2)

    error = 0.1 * np.abs(guess) * np.ones_like(guess)
    fix = [False] * len(guess)
    fix[-1] = True

    consecutive_outliers = []
    for step in range(niter):
        # noinspection PyArgumentList
        # it seems that minuit with a jacobian function works less good...
        m = Minuit.from_array_func(fcn=PSF1D_chisq_v2, start=guess, error=error, errordef=1, limit=bounds, fix=fix,
                                   print_level=0, grad=None)
        m.migrad()
        guess = m.np_values()
        psf = PSF1DAstropy(*m.np_values())
        for ip, p in enumerate(model.param_names):
            setattr(model, p, guess[ip])
        # remove outliers
        indices_no_nan = ~np.isnan(data)
        diff = model(x[indices_no_nan]) - data[indices_no_nan]
        if data_errors is not None:
            outliers = np.where(np.abs(diff) / data_errors[indices_no_nan] > sigma)[0]
        else:
            std = np.std(diff)
            outliers = np.where(np.abs(diff) / std > sigma)[0]
        if len(outliers) > 0:
            # test if 3 consecutive pixels are in the outlier list
            test = 0
            consecutive_outliers = []
            for o in range(1, len(outliers)):
                t = outliers[o] - outliers[o - 1]
                if t == 1:
                    test += t
                else:
                    test = 0
                if test >= consecutive - 1:
                    for i in range(consecutive):
                        consecutive_outliers.append(outliers[o - i])
            consecutive_outliers = list(set(consecutive_outliers))
            # my_logger.debug(f"\n\tConsecutive oultlier indices: {consecutive_outliers}")
            indices = [i for i in range(x.shape[0]) if i not in outliers]
        else:
            break

    # my_logger.debug(f'\n\tPSF best fitting parameters:\n{PSF}')
    return psf, consecutive_outliers


@deprecated(reason="Use new MoffatGauss2D class.")
class PSF2DAstropy(Fittable2DModel):
    n_inputs = 2
    n_outputs = 1
    # inputs = ('x', 'y',)
    # outputs = ('z',)

    amplitude_moffat = Parameter('amplitude_moffat', default=1)
    x_mean = Parameter('x_mean', default=0)
    y_mean = Parameter('y_mean', default=0)
    gamma = Parameter('gamma', default=3)
    alpha = Parameter('alpha', default=3)
    eta_gauss = Parameter('eta_gauss', default=0.)
    stddev = Parameter('stddev', default=1)
    saturation = Parameter('saturation', default=1)

    param_titles = ["A", "x", "y", r"\gamma", r"\alpha", r"\eta", r"\sigma", "saturation"]

    @staticmethod
    def evaluate(x, y, amplitude, x_mean, y_mean, gamma, alpha, eta_gauss, stddev, saturation):
        rr = ((x - x_mean) ** 2 + (y - y_mean) ** 2)
        rr_gg = rr / (gamma * gamma)
        a = amplitude * ((1 + rr_gg) ** (-alpha) + eta_gauss * np.exp(-(rr / (2. * stddev * stddev))))
        return np.clip(a, 0, saturation)

    @staticmethod
    def normalisation(amplitude, gamma, alpha, eta_gauss, stddev):
        return amplitude * ((np.pi * gamma * gamma) / (alpha - 1) + eta_gauss * 2 * np.pi * stddev * stddev)

    @staticmethod
    def fit_deriv(x, y, amplitude, x_mean, y_mean, gamma, alpha, eta_gauss, stddev, saturation):
        rr = ((x - x_mean) ** 2 + (y - y_mean) ** 2)
        rr_gg = rr / (gamma * gamma)
        gauss_norm = np.exp(-(rr / (2. * stddev * stddev)))
        d_eta_gauss = amplitude * gauss_norm
        moffat_norm = (1 + rr_gg) ** (-alpha)
        d_amplitude = moffat_norm + eta_gauss * gauss_norm
        d_x_mean = amplitude * eta_gauss * (x - x_mean) / (stddev * stddev) * gauss_norm \
                   - amplitude * alpha * moffat_norm * (-2 * x + 2 * x_mean) / (gamma ** 2 * (1 + rr_gg))
        d_y_mean = amplitude * eta_gauss * (y - y_mean) / (stddev * stddev) * gauss_norm \
                   - amplitude * alpha * moffat_norm * (-2 * y + 2 * y_mean) / (gamma ** 2 * (1 + rr_gg))
        d_stddev = amplitude * eta_gauss * rr / (stddev ** 3) * gauss_norm
        d_alpha = - amplitude * moffat_norm * np.log(1 + rr_gg)
        d_gamma = 2 * amplitude * alpha * moffat_norm * (rr_gg / (gamma * (1 + rr_gg)))
        d_saturation = saturation * np.zeros_like(x)
        return [d_amplitude, d_x_mean, d_y_mean, d_gamma, d_alpha, d_eta_gauss, d_stddev, d_saturation]

    def interpolation(self, x_array, y_array):
        """

        Parameters
        ----------
        x_array: array_like
            The x array to interpolate the model.
        y_array: array_like
            The y array to interpolate the model.

        Returns
        -------
        interp: callable
            Function corresponding to the interpolated model on the (x_array,y_array) array.

        Examples
        --------
        >>> x = np.arange(0, 60, 1)
        >>> y = np.arange(0, 30, 1)
        >>> p = [2,30,15,2,2,1,2,10]
        >>> psf = PSF2DAstropy(*p)
        >>> interp = psf.interpolation(x, y)

        ..  doctest::
            :hide:

            >>> assert np.isclose(interp(p[1], p[2]), psf.evaluate(p[1], p[2], *p))

        """
        params = [getattr(self, p).value for p in self.param_names]
        xx, yy = np.meshgrid(x_array, y_array)
        return interp2d(x_array, y_array, self.evaluate(xx, yy, *params), fill_value=0, bounds_error=False)


if __name__ == "__main__":
    import doctest

    doctest.testmod()
