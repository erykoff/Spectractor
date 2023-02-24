import time
import os
import matplotlib.pyplot as plt
from matplotlib import cm
import numpy as np
from scipy.signal import convolve2d
import copy

from spectractor import parameters
from spectractor.config import set_logger
from spectractor.tools import plot_image_simple, from_lambda_to_colormap
from spectractor.simulation.simulator import SimulatorInit, SpectrogramModel
from spectractor.simulation.atmosphere import Atmosphere, AtmosphereGrid
from spectractor.fit.fitter import FitWorkspace, run_minimisation, run_minimisation_sigma_clipping

plot_counter = 0


class SpectrogramFitWorkspace(FitWorkspace):

    def __init__(self, file_name, atmgrid_file_name="", fit_angstrom_exponent=False, nwalkers=18, nsteps=1000, burnin=100, nbins=10,
                 verbose=0, plot=False, live_fit=False, truth=None):
        """Class to fit a spectrogram extracted with Spectractor.

        First the spectrogram is cropped using the parameters.PIXWIDTH_SIGNAL parameter to increase speedness.
        The truth parameters are loaded from the file header if provided.
        If provided, the atmospheric grid is used for the atmospheric transmission simulations and interpolated
        with splines, otherwise Libradtran is called at each step (slower).

        Parameters
        ----------
        file_name: str
            Spectrum file name.
        atmgrid_file_name: str, optional
            Atmospheric grid file name (default: "").
        fit_angstrom_exponent: bool, optional
            If True, fit angstrom exponent (default: False).
        nwalkers: int, optional
            Number of walkers for MCMC fitting.
        nsteps: int, optional
            Number of steps for MCMC fitting.
        burnin: int, optional
            Number of burn-in steps for MCMC fitting.
        nbins: int, optional
            Number of bins for MCMC chains analysis.
        verbose: int, optional
            Verbosity level (default: 0).
        plot: bool, optional
            If True, many plots are produced (default: False).
        live_fit: bool, optional
            If True, many plots along the fitting procedure are produced to see convergence in live (default: False).
        truth: array_like, optional
            Array of truth parameters to compare with the best fit result (default: None).

        Examples
        --------

        >>> from spectractor.config import load_config
        >>> load_config("config/ctio.ini")
        >>> filename = 'tests/data/reduc_20170530_134_spectrum.fits'
        >>> atmgrid_filename = filename.replace('spectrum', 'atmsim')
        >>> w = SpectrogramFitWorkspace(filename, atmgrid_file_name=atmgrid_filename, nsteps=1000,
        ... burnin=2, nbins=10, verbose=1, plot=True, live_fit=False)
        >>> lambdas, model, model_err = w.simulate(*w.p)
        >>> w.plot_fit()

        """
        FitWorkspace.__init__(self, file_name, nwalkers, nsteps, burnin, nbins, verbose, plot,
                              live_fit, truth=truth)
        if "spectrum" not in file_name:
            raise ValueError("file_name argument must contain spectrum keyword and be an output from Spectractor.")
        if fit_angstrom_exponent and atmgrid_file_name != '':
            raise ValueError(f"AtmosphereGrid() do not contains angstrom exponent modelisation yet. "
                             f"Can't provide atmgrid_file_name and ask for fitting angstrom exponent {fit_angstrom_exponent=}.")
        self.filename = self.filename.replace("spectrum", "spectrogram")
        self.spectrum, self.telescope, self.disperser, self.target = SimulatorInit(file_name)
        self.airmass = self.spectrum.header['AIRMASS']
        self.pressure = self.spectrum.header['OUTPRESS']
        self.temperature = self.spectrum.header['OUTTEMP']
        self.my_logger = set_logger(self.__class__.__name__)
        if atmgrid_file_name == "":
            self.atmosphere = Atmosphere(self.airmass, self.pressure, self.temperature)
        else:
            self.use_grid = True
            self.atmosphere = AtmosphereGrid(spectrum_filename=file_name, atmgrid_filename=atmgrid_file_name)
            if parameters.VERBOSE:
                self.my_logger.info(f'\n\tUse atmospheric grid models from file {atmgrid_file_name}. ')
        self.crop_spectrogram()
        self.lambdas = self.spectrum.lambdas
        self.Ny, self.Nx = self.spectrum.spectrogram.shape
        self.data = self.spectrum.spectrogram.flatten()
        self.err = self.spectrum.spectrogram_err.flatten()
        self.fit_angstrom_exponent = fit_angstrom_exponent
        self.A1 = 1.0
        self.A2 = 1.0
        self.ozone = 400.
        self.pwv = 5
        self.aerosols = 0.05
        self.angstrom_exponent_log10 = -2
        self.D = self.spectrum.header['D2CCD']
        self.psf_poly_params = self.spectrum.chromatic_psf.from_table_to_poly_params()
        length = len(self.spectrum.chromatic_psf.table)
        self.psf_poly_params = self.psf_poly_params[length:]
        self.psf_poly_params_labels = np.copy(self.spectrum.chromatic_psf.poly_params_labels[length:])
        self.psf_poly_params_names = np.copy(self.spectrum.chromatic_psf.poly_params_names[length:])
        self.psf_poly_params_bounds = self.spectrum.chromatic_psf.set_bounds_for_minuit(data=None)
        self.spectrum.chromatic_psf.psf.apply_max_width_to_bounds(max_half_width=self.spectrum.spectrogram_Ny)
        psf_poly_params_bounds = self.spectrum.chromatic_psf.set_bounds()
        self.shift_x = self.spectrum.header['PIXSHIFT']
        self.shift_y = 0.
        self.angle = self.spectrum.rotation_angle
        self.B = 1
        self.saturation = self.spectrum.spectrogram_saturation
        self.p = np.array([self.A1, self.A2, self.aerosols, self.angstrom_exponent_log10, self.ozone, self.pwv,
                           self.D, self.shift_x, self.shift_y, self.angle, self.B])
        self.fixed_psf_params = np.array([0, 1, 2, 3, 4, 5, 9])
        self.atm_params_indices = np.array([2, 3, 4, 5])
        self.psf_params_start_index = self.p.size
        self.p = np.concatenate([self.p, self.psf_poly_params, np.copy(self.psf_poly_params)])
        self.input_labels = ["A1", "A2", "VAOD", "angstrom_exp_log10", "ozone [db]", "PWV [mm]", r"D_CCD [mm]",
                             r"shift_x [pix]", r"shift_y [pix]", r"angle [deg]", "B"] + \
                            list(self.psf_poly_params_labels) + [label+"_2" for label in self.psf_poly_params_labels]
        self.axis_names = ["$A_1$", "$A_2$", "VAOD", r'$\log_{10}\"a$', "ozone [db]", "PWV [mm]", r"$D_{CCD}$ [mm]",
                           r"$\Delta_{\mathrm{x}}$ [pix]", r"$\Delta_{\mathrm{y}}$ [pix]",
                           r"$\theta$ [deg]", "$B$"] + \
                          list(self.psf_poly_params_names) + [label+"_2" for label in self.psf_poly_params_names]
        bounds_D = (self.D - 5 * parameters.DISTANCE2CCD_ERR, self.D + 5 * parameters.DISTANCE2CCD_ERR)
        self.bounds = np.concatenate([np.array([(0, 2), (0, 2/parameters.GRATING_ORDER_2OVER1), (0, 0.1), (-5, 2),
                                                (100, 700), (0, 10), bounds_D, (-2, 2), (-10, 10), (-90, 90), (0.8, 1.2)]),
                                      list(psf_poly_params_bounds) * 2])
        self.fixed = [False] * self.p.size
        for k, par in enumerate(self.input_labels):
            if "x_c" in par or "saturation" in par: # or "y_c" in par:
                self.fixed[k] = True
        for k, par in enumerate(self.input_labels):
            if "y_c" in par:
                self.fixed[k] = False
                self.p[k] = 0
        # A2 is free only if spectrogram is a simulation or if the order 2/1 ratio is not known and flat
        self.fixed[1] = "A2_T" not in self.spectrum.header  # not self.spectrum.disperser.flat_ratio_order_2over1
        # self.fixed[5:7] = [True, True]  # DCCD, x0
        self.fixed[7] = True  # Delta x
        self.fixed[8] = True  # Delta y
        self.fixed[9] = True  # angle
        self.fixed[10] = True  # B
        if not fit_angstrom_exponent:
            self.fixed[3] = True  # angstrom exponent
        if atmgrid_file_name != "":
            self.bounds[2] = (min(self.atmosphere.AER_Points), max(self.atmosphere.AER_Points))
            self.bounds[4] = (min(self.atmosphere.OZ_Points), max(self.atmosphere.OZ_Points))
            self.bounds[5] = (min(self.atmosphere.PWV_Points), max(self.atmosphere.PWV_Points))
            self.fixed[3] = True  # angstrom exponent
        self.nwalkers = max(2 * self.ndim, nwalkers)
        self.simulation = SpectrogramModel(self.spectrum, self.atmosphere, self.telescope, self.disperser,
                                           with_background=True, fast_sim=False, with_adr=True)
        self.lambdas_truth = None
        self.amplitude_truth = None
        self.get_spectrogram_truth()

        # PSF cube computation
        self.psf_cube_masked = None
        self.psf_cube = None
        self.psf_cube_order2 = None
        self.fix_psf_cube = False
        self.fix_psf_cube_order2 = False
        self.psf_params_index = np.arange(0, self.psf_params_start_index+len(self.psf_poly_params))
        self.psf_params_index_order2 = np.concatenate([np.arange(0, self.psf_params_start_index), np.arange(np.max(self.psf_params_index)+1, len(self.p))])
        self.psf_params_start_index_order2 = np.max(self.psf_params_index)+1

        # error matrix
        # here image uncertainties are assumed to be uncorrelated
        # (which is not exactly true in rotated images)
        self.W = 1. / (self.err * self.err)
        self.W = self.W.flatten()

        # flat data for fitworkspace
        self.data_before_mask = np.copy(self.data)
        self.W_before_mask = np.copy(self.W)
        # create mask
        self.set_mask()

    def crop_spectrogram(self):
        """Crop the spectrogram in the middle, keeping a vertical width of 2*parameters.PIXWIDTH_SIGNAL around
        the signal region.

        """
        bgd_width = parameters.PIXWIDTH_BACKGROUND + parameters.PIXDIST_BACKGROUND - parameters.PIXWIDTH_SIGNAL
        self.spectrum.spectrogram_ymax = self.spectrum.spectrogram_ymax - bgd_width
        self.spectrum.spectrogram_ymin += bgd_width
        self.spectrum.spectrogram_bgd = self.spectrum.spectrogram_bgd[bgd_width:-bgd_width, :]
        self.spectrum.spectrogram = self.spectrum.spectrogram[bgd_width:-bgd_width, :]
        self.spectrum.spectrogram_err = self.spectrum.spectrogram_err[bgd_width:-bgd_width, :]
        self.spectrum.spectrogram_y0 -= bgd_width
        self.spectrum.chromatic_psf.y0 -= bgd_width
        self.spectrum.spectrogram_Ny, self.spectrum.spectrogram_Nx = self.spectrum.spectrogram.shape
        self.spectrum.chromatic_psf.table["y_c"] -= bgd_width
        self.my_logger.debug(f'\n\tSize of the spectrogram region after cropping: '
                             f'({self.spectrum.spectrogram_Nx},{self.spectrum.spectrogram_Ny})')

    def set_mask(self, params=None):
        """

        Parameters
        ----------
        params

        Returns
        -------

        Examples
        --------
        >>> from spectractor.config import load_config
        >>> load_config("config/ctio.ini")
        >>> filename = 'tests/data/reduc_20170530_134_spectrum.fits'
        >>> w = SpectrogramFitWorkspace(filename, verbose=True)
        >>> _ = w.simulate(*w.p)
        >>> w.plot_fit()

        """
        self.my_logger.info("\n\tReset spectrogram mask with current parameters.")
        if params is None:
            params = self.p
        A1, A2, aerosols, angstrom_exponent, ozone, pwv, D, shift_x, shift_y, angle, B, *psf_poly_params = params
        psf_profile_params = self.spectrum.chromatic_psf.from_poly_params_to_profile_params(psf_poly_params,
                                                                                            apply_bounds=True)
        self.spectrum.chromatic_psf.from_profile_params_to_shape_params(psf_profile_params)
        Dx = np.arange(len(psf_profile_params[:,
                           0])) - self.spectrum.spectrogram_x0 - shift_x  # distance in (x,y) spectrogram frame for column x
        Dy_disp_axis = np.tan(angle * np.pi / 180) * Dx  # disp axis height in spectrogram frame for x
        psf_profile_params[:, 0] = 1
        psf_profile_params[:, 1] = Dx + self.spectrum.spectrogram_x0 + shift_x
        psf_profile_params[:, 2] = Dy_disp_axis + (self.spectrum.spectrogram_y0 + shift_y)  # - self.bgd_width
        psf_cube = self.spectrum.chromatic_psf.build_psf_cube(self.simulation.pixels, psf_profile_params,
                                                              fwhmx_clip=3 * parameters.PSF_FWHM_CLIP,
                                                              fwhmy_clip=parameters.PSF_FWHM_CLIP, dtype="float32")
        self.simulation.psf_cube_masked = psf_cube > 0
        flat_spectrogram = np.sum(self.simulation.psf_cube_masked.reshape(len(psf_profile_params), self.simulation.pixels[0].size),
                                  axis=0)
        mask = flat_spectrogram == 0  # < 1e-2 * np.max(flat_spectrogram)
        mask = mask.reshape(self.simulation.pixels[0].shape)
        kernel = np.ones((3, self.spectrum.spectrogram_Nx//10))  # enlarge a bit more the edges of the mask
        mask = convolve2d(mask, kernel, 'same').astype(bool)
        for k in range(self.simulation.psf_cube_masked.shape[0]):
            self.simulation.psf_cube_masked[k] *= ~mask
        mask = mask.reshape((self.simulation.pixels[0].size,))
        self.W = np.copy(self.W_before_mask)
        self.W[mask] = 0
        self.mask = list(np.where(mask)[0])

    def get_spectrogram_truth(self):
        """Load the truth parameters (if provided) from the file header.

        """
        if 'A1_T' in list(self.spectrum.header.keys()):
            A1_truth = self.spectrum.header['A1_T']
            A2_truth = self.spectrum.header['A2_T']
            ozone_truth = self.spectrum.header['OZONE_T']
            pwv_truth = self.spectrum.header['PWV_T']
            aerosols_truth = self.spectrum.header['VAOD_T']
            D_truth = self.spectrum.header['D2CCD_T']
            shiftx_truth = 0
            shifty_truth = 0
            rotation_angle = self.spectrum.header['ROT_T']
            B = 1
            poly_truth = np.fromstring(self.spectrum.header['PSF_P_T'][1:-1], sep=' ', dtype=float)
            self.truth = (A1_truth, A2_truth, aerosols_truth, ozone_truth, pwv_truth,
                          D_truth, shiftx_truth, shifty_truth, rotation_angle, B, *poly_truth)
            self.lambdas_truth = np.fromstring(self.spectrum.header['LBDAS_T'][1:-1], sep=' ', dtype=float)
            self.amplitude_truth = np.fromstring(self.spectrum.header['AMPLIS_T'][1:-1], sep=' ', dtype=float)
        else:
            self.truth = None

    def plot_spectrogram_comparison_simple(self, ax, title='', extent=None, dispersion=False):
        """Method to plot a spectrogram issued from data and compare it with simulations.

        Parameters
        ----------
        ax: Axes
            Axes instance of shape (4, 2).
        title: str, optional
            Title for the simulation plot (default: '').
        extent: array_like, optional
            Extent argument for imshow to crop plots (default: None).
        dispersion: bool, optional
            If True, plot a colored bar to see the associated wavelength color along the x axis (default: False).
        """
        cmap_bwr = copy.copy(cm.get_cmap('bwr'))
        cmap_bwr.set_bad(color='lightgrey')
        cmap_viridis = copy.copy(cm.get_cmap('viridis'))
        cmap_viridis.set_bad(color='lightgrey')

        data = np.copy(self.data_before_mask)
        if len(self.outliers) > 0 or len(self.mask) > 0:
            bad_indices = np.array(list(self.get_bad_indices()) + list(self.mask)).astype(int)
            data[bad_indices] = np.nan

        lambdas = self.spectrum.lambdas
        sub = np.where((lambdas > parameters.LAMBDA_MIN) & (lambdas < parameters.LAMBDA_MAX))[0]
        sub = np.where(sub < self.spectrum.spectrogram_Nx)[0]
        data = data.reshape((self.Ny, self.Nx))
        model = self.model.reshape((self.Ny, self.Nx))
        err = self.err.reshape((self.Ny, self.Nx))
        if extent is not None:
            sub = np.where((lambdas > extent[0]) & (lambdas < extent[1]))[0]
        if len(sub) > 0:
            norm = np.nanmax(data[:, sub])
            plot_image_simple(ax[0, 0], data=data[:, sub] / norm, title='Data', aspect='auto',
                              cax=ax[0, 1], vmin=0, vmax=1, units='1/max(data)', cmap=cmap_viridis)
            ax[0, 0].set_title('Data', fontsize=10, loc='center', color='white', y=0.8)
            plot_image_simple(ax[1, 0], data=model[:, sub] / norm, aspect='auto', cax=ax[1, 1], vmin=0, vmax=1,
                              units='1/max(data)', cmap=cmap_viridis)
            if dispersion:
                x = self.spectrum.chromatic_psf.table['Dx'][sub[5:-5]] + self.spectrum.spectrogram_x0 - sub[0]
                y = np.ones_like(x)
                ax[1, 0].scatter(x, y, cmap=from_lambda_to_colormap(self.lambdas[sub[5:-5]]), edgecolors='None',
                                 c=self.lambdas[sub[5:-5]],
                                 label='', marker='o', s=10)
                ax[1, 0].set_xlim(0, model[:, sub].shape[1])
                ax[1, 0].set_ylim(0, model[:, sub].shape[0])
            # p0 = ax.plot(lambdas, self.model(lambdas), label='model')
            # # ax.plot(self.lambdas, self.model_noconv, label='before conv')
            if title != '':
                ax[1, 0].set_title(title, fontsize=10, loc='center', color='white', y=0.8)
            residuals = (data - model)
            # residuals_err = self.spectrum.spectrogram_err / self.model
            norm = np.sqrt(err**2 + self.model_err.reshape((self.Ny, self.Nx))**2)
            residuals /= norm
            std = float(np.nanstd(residuals[:, sub]))
            plot_image_simple(ax[2, 0], data=residuals[:, sub], vmin=-5 * std, vmax=5 * std, title='(Data-Model)/Err',
                              aspect='auto', cax=ax[2, 1], units='', cmap=cmap_bwr)
            ax[2, 0].set_title('(Data-Model)/Err', fontsize=10, loc='center', color='black', y=0.8)
            ax[2, 0].text(0.05, 0.05, f'mean={np.nanmean(residuals[:, sub]):.3f}\nstd={np.nanstd(residuals[:, sub]):.3f}',
                          horizontalalignment='left', verticalalignment='bottom',
                          color='black', transform=ax[2, 0].transAxes)
            ax[0, 0].set_xticks(ax[2, 0].get_xticks()[1:-1])
            ax[0, 1].get_yaxis().set_label_coords(3.5, 0.5)
            ax[1, 1].get_yaxis().set_label_coords(3.5, 0.5)
            ax[2, 1].get_yaxis().set_label_coords(3.5, 0.5)
            ax[3, 1].remove()
            ax[3, 0].plot(self.lambdas[sub], np.nansum(data, axis=0)[sub], label='Data')
            ax[3, 0].plot(self.lambdas[sub], np.nansum(model, axis=0)[sub], label='Model')
            ax[3, 0].set_ylabel('Cross spectrum')
            ax[3, 0].set_xlabel(r'$\lambda$ [nm]')
            ax[3, 0].legend(fontsize=7)
            ax[3, 0].grid(True)

    def simulate(self, A1, A2, aerosols, angstrom_exponent_log10, ozone, pwv, D, shift_x, shift_y, angle, B, *psf_poly_params):
        """Interface method to simulate a spectrogram.

        Parameters
        ----------
        A1: float
            Main amplitude parameter.
        A2: float
            Relative amplitude of the order 2 spectrogram.
        aerosols: float
            Vertical Aerosols Optical Depth quantity for Libradtran (no units).
        angstrom_exponent_log10: float
            Logarithm base 10 of Angstrom exponent for aerosols.
        ozone: float
            Ozone parameter for Libradtran (in db).
        pwv: float
            Precipitable Water Vapor quantity for Libradtran (in mm).
        D: float
            Distance between the CCD and the disperser (in mm).
        shift_x: float
            Shift of the order 0 position along the X axis (in pixels).
        shift_y: float
            Shift of the order 0 position along the Y axis (in pixels).
        angle: float
            Angle of the dispersion axis with respect to the X axis (in degrees).
        B: float
            Amplitude of the simulated background.
        psf_poly_params: array_like
            PSF polynomial parameters formatted with the ChromaticPSF class.

        Returns
        -------
        lambdas: array_like
            Array of wavelengths (1D).
        model: array_like
            Flat 1D array of the spectrogram simulation.
        model_err: array_like
            Flat 1D array of the spectrogram simulation uncertainty.

        Examples
        --------

        >>> from spectractor.config import load_config
        >>> filename = 'tests/data/reduc_20170530_134_spectrum.fits'
        >>> atmgrid_filename = filename.replace('spectrum', 'atmsim')
        >>> load_config("config/ctio.ini")
        >>> w = SpectrogramFitWorkspace(filename, atmgrid_filename, verbose=1, plot=True, live_fit=False)
        >>> lambdas, model, model_err = w.simulate(*w.p)
        >>> w.plot_fit()

        """
        global plot_counter
        if self.fit_angstrom_exponent:
            angstrom_exponent = 10 ** angstrom_exponent_log10
        else:
            angstrom_exponent = None
        lambdas, model, model_err = \
            self.simulation.simulate(A1, A2, aerosols, angstrom_exponent, ozone, pwv, D, shift_x, shift_y, angle, B, psf_poly_params)
        self.p = np.array([A1, A2, aerosols, angstrom_exponent, ozone, pwv, D, shift_x, shift_y, angle, B] + list(psf_poly_params))
        self.lambdas = lambdas
        self.model = model.flatten()
        self.model_err = model_err.flatten()
        if self.live_fit and (plot_counter % 30) == 0:  # pragma: no cover
            self.plot_fit()
        plot_counter += 1
        return self.lambdas, self.model, self.model_err

    def jacobian(self, params, epsilon, fixed_params=None, model_input=None):
        start = time.time()
        if model_input is not None:
            lambdas, model, model_err = model_input
        else:
            lambdas, model, model_err = self.simulate(*params)
        model = model.flatten()
        J = np.zeros((params.size, model.size))
        strategy = copy.copy(self.simulation.fix_psf_cube)
        atmosphere = copy.copy(self.simulation.atmosphere_sim)
        for ip, p in enumerate(params):
            if fixed_params[ip]:
                continue
            if ip in self.fixed_psf_params:
                self.simulation.fix_psf_cube = True
            else:
                self.simulation.fix_psf_cube = False
            if ip in self.atm_params_indices:
                self.simulation.fix_atm_sim = False
            else:
                self.simulation.fix_atm_sim = True
            tmp_p = np.copy(params)
            if tmp_p[ip] + epsilon[ip] < self.bounds[ip][0] or tmp_p[ip] + epsilon[ip] > self.bounds[ip][1]:
                epsilon[ip] = - epsilon[ip]
            tmp_p[ip] += epsilon[ip]
            tmp_lambdas, tmp_model, tmp_model_err = self.simulate(*tmp_p)
            if self.simulation.fix_atm_sim is False:
                self.simulation.atmosphere_sim = atmosphere
            J[ip] = (tmp_model.flatten() - model) / epsilon[ip]
        self.simulation.fix_psf_cube = strategy
        self.simulation.fix_atm_sim = False
        self.my_logger.debug(f"\n\tJacobian time computation = {time.time() - start:.1f}s")
        return J

    def plot_fit(self):
        """Plot the fit result.

        Examples
        --------

        >>> from spectractor.config import load_config
        >>> load_config("config/ctio.ini")
        >>> filename = 'tests/data/reduc_20170530_134_spectrum.fits'
        >>> atmgrid_filename = filename.replace('spectrum', 'atmsim')
        >>> w = SpectrogramFitWorkspace(filename, atmgrid_filename, verbose=1, plot=True, live_fit=False)
        >>> lambdas, model, model_err = w.simulate(*w.p)
        >>> w.plot_fit()

        .. plot::
            :include-source:

            from spectractor.fit.fit_spectrogram import SpectrogramFitWorkspace
            file_name = 'tests/data/reduc_20170530_134_spectrum.fits'
            atmgrid_file_name = file_name.replace('spectrum', 'atmsim')
            fit_workspace = SpectrogramFitWorkspace(file_name, atmgrid_file_name=atmgrid_file_name, verbose=True)
            A1, A2, aerosols, ozone, pwv, D, shift_x, shift_y, angle, *psf = fit_workspace.p
            lambdas, model, model_err = fit_workspace.simulation.simulate(A1, A2, aerosols, ozone, pwv, D, shift_x,
                                                                          shift_y, angle, psf)
            fit_workspace.lambdas = lambdas
            fit_workspace.model = model
            fit_workspace.model_err = model_err
            fit_workspace.plot_fit()

        """
        gs_kw = dict(width_ratios=[3, 0.01, 1, 0.01, 1, 0.15], height_ratios=[1, 1, 1, 1])
        fig, ax = plt.subplots(nrows=4, ncols=6, figsize=(10, 8), gridspec_kw=gs_kw)

        # A1, A2, aerosols, ozone, pwv, D, shift_x, shift_y, shift_t, B,  *psf = self.p
        # plt.suptitle(f'A1={A1:.3f}, A2={A2:.3f}, PWV={pwv:.3f}, OZ={ozone:.3g}, VAOD={aerosols:.3f}, '
        #              f'D={D:.2f}mm, shift_y={shift_y:.2f}pix, B={B:.3f}', y=1)
        # main plot
        self.plot_spectrogram_comparison_simple(ax[:, 0:2], title='Spectrogram model', dispersion=True)
        # zoom O2
        self.plot_spectrogram_comparison_simple(ax[:, 2:4], extent=[730, 800], title='Zoom $O_2$', dispersion=True)
        # zoom H2O
        self.plot_spectrogram_comparison_simple(ax[:, 4:6], extent=[870, 1000], title='Zoom $H_2 O$', dispersion=True)
        for i in range(3):  # clear middle colorbars
            for j in range(2):
                plt.delaxes(ax[i, 2*j+1])
        for i in range(4):  # clear middle y axis labels
            for j in range(1, 3):
                ax[i, 2*j].set_ylabel("")
        fig.tight_layout()
        if self.live_fit:  # pragma: no cover
            plt.draw()
            plt.pause(1e-8)
            plt.close()
        else:
            if parameters.DISPLAY and self.verbose:
                plt.show()
        if parameters.PdfPages:
            parameters.PdfPages.savefig()
        if parameters.SAVE:
            figname = os.path.splitext(self.filename)[0] + "_bestfit.pdf"
            self.my_logger.info(f"\n\tSave figure {figname}.")
            fig.savefig(figname, dpi=100, bbox_inches='tight', transparent=True)


def lnprob_spectrogram(p):  # pragma: no cover
    """Logarithmic likelihood function to maximize in MCMC exploration.

    Parameters
    ----------
    p: array_like
        Array of SpectrogramFitWorkspace parameters.

    Returns
    -------
    lp: float
        Log of the likelihood function.

    """
    global fit_workspace
    lp = fit_workspace.lnprior(p)
    if not np.isfinite(lp):
        return -1e20
    return lp + fit_workspace.lnlike_spectrogram(p)


def run_spectrogram_minimisation(fit_workspace, method="newton"):
    """Interface function to fit spectrogram simulation parameters to data.

    Parameters
    ----------
    fit_workspace: SpectrogramFitWorkspace
        An instance of the SpectrogramFitWorkspace class.
    method: str, optional
        Fitting method (default: 'newton').

    Examples
    --------

    >>> from spectractor.config import load_config
    >>> load_config("config/ctio.ini")
    >>> filename = 'tests/data/sim_20170530_134_spectrum.fits'
    >>> atmgrid_filename = filename.replace('sim', 'reduc').replace('spectrum', 'atmsim')
    >>> w = SpectrogramFitWorkspace(filename, atmgrid_file_name=atmgrid_filename, verbose=1, plot=True, live_fit=False)
    >>> parameters.VERBOSE = True
    >>> run_spectrogram_minimisation(w, method="newton")

    """
    my_logger = set_logger(__name__)
    guess = np.asarray(fit_workspace.p)
    fit_workspace.simulate(*guess)
    fit_workspace.plot_fit()
    if method != "newton":
        run_minimisation(fit_workspace, method=method)
    else:
        # costs = np.array([fit_workspace.chisq(guess)])
        # if parameters.DISPLAY and (parameters.DEBUG or fit_workspace.live_fit):
        #     fit_workspace.plot_fit()
        # params_table = np.array([guess])
        start = time.time()
        my_logger.info(f"\n\tStart guess: {guess}\n\twith {fit_workspace.input_labels}")
        epsilon = 1e-4 * guess
        epsilon[epsilon == 0] = 1e-4
        fixed = np.copy(fit_workspace.fixed)

        # fit_workspace.simulation.fast_sim = True
        # fit_workspace.simulation.fix_psf_cube = False
        # fit_workspace.fixed = np.copy(fixed)
        # fit_workspace.fixed[:fit_workspace.psf_params_start_index] = True
        # params_table, costs = run_gradient_descent(fit_workspace, guess, epsilon, params_table, costs,
        #                                            fix=fit_workspace.fixed, xtol=1e-3, ftol=1e-2, niter=10)

        # fit_workspace.simulation.fast_sim = True
        # fit_workspace.simulation.fix_psf_cube = False
        # fit_workspace.fixed = np.copy(fixed)
        # for ip, label in enumerate(fit_workspace.input_labels):
        #     if "y_c_0" in label:
        #         fit_workspace.fixed[ip] = False
        #     else:
        #         fit_workspace.fixed[ip] = True
        # run_minimisation(fit_workspace, method="newton", epsilon=epsilon, fix=fit_workspace.fixed,
        #                  xtol=1e-2, ftol=10 / fit_workspace.data.size, verbose=False)

        fit_workspace.simulation.fast_sim = False
        fit_workspace.simulation.fix_psf_cube = False
        fit_workspace.fixed = np.copy(fixed)
        # guess = fit_workspace.p
        # params_table, costs = run_gradient_descent(fit_workspace, guess, epsilon, params_table, costs,
        #                                            fix=fit_workspace.fixed, xtol=1e-6, ftol=1 / fit_workspace.data.size,
        #                                            niter=40)
        run_minimisation_sigma_clipping(fit_workspace, method="newton", epsilon=epsilon, fix=fit_workspace.fixed,
                                        xtol=1e-6, ftol=1 / fit_workspace.data.size, sigma_clip=100, niter_clip=3,
                                        verbose=False)
        my_logger.info(f"\n\tNewton: total computation time: {time.time() - start}s")
        if fit_workspace.filename != "":
            parameters.SAVE = True
            ipar = np.array(np.where(np.array(fit_workspace.fixed).astype(int) == 0)[0])
            fit_workspace.plot_correlation_matrix(ipar)
            fit_workspace.save_parameters_summary(ipar, header=f"{fit_workspace.spectrum.date_obs}\n"
                                                               f"chi2: "
                                                               f"{fit_workspace.costs[-1] / fit_workspace.data.size}")
            # save_gradient_descent(fit_workspace, costs, params_table)
            fit_workspace.plot_fit()
            parameters.SAVE = False


if __name__ == "__main__":
    import doctest

    doctest.testmod()
