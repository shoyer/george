# -*- coding: utf-8 -*-

from __future__ import division, print_function

__all__ = ["GP"]

try:
    from itertools import izip
except ImportError:
    izip = zip

import numpy as np
import scipy.optimize as op
from scipy.linalg import cholesky, cho_solve, LinAlgError

from .utils import multivariate_gaussian_samples, nd_sort_samples


# MAGIC: tiny epsilon to add on the diagonal of the matrices in the absence
# of observational uncertainties. Needed for computational stability.
TINY = 1.25e-12


class GP(object):
    """
    The basic Gaussian Process object.

    :param kernel:
        An instance of a subclass of :class:`kernels.Kernel`.

    :param mean: (optional)
        A description of the mean function; can be a callable or a scalar. If
        scalar, the mean is assumed constant. Otherwise, the function will be
        called with the array of independent coordinates as the only argument.
        (default: ``0.0``)

    """

    def __init__(self, kernel, mean=None):
        self.kernel = kernel
        self._computed = False
        self.mean = mean

    @property
    def mean(self):
        return self._mean

    @mean.setter
    def mean(self, mean):
        if mean is None:
            self._mean = _default_mean(0.)
        else:
            try:
                val = float(mean)
            except TypeError:
                self._mean = mean
            else:
                self._mean = _default_mean(val)

    @property
    def computed(self):
        """
        Has the processes been computed since the last update of the kernel?

        """
        return self._computed and not self.kernel.dirty

    @computed.setter
    def computed(self, v):
        self._computed = v
        if v:
            self.kernel.dirty = False

    def parse_samples(self, t, sort=False):
        """
        Parse a list of samples to make sure that it has the correct
        dimensions and optionally sort it. In one dimension, the samples will
        be sorted in the logical order. In higher dimensions, a kd-tree is
        built and the samples are sorted in increasing distance from the
        *first* sample.

        :param t: ``(nsamples,)`` or ``(nsamples, ndim)``
            The list of samples. If 1-D, this is assumed to be a list of
            one-dimensional samples otherwise, the size of the second
            dimension is assumed to be the dimension of the input space.

        :param sort:
            A boolean flag indicating whether or not the samples should be
            sorted.

        Returns a tuple ``(samples, inds)`` where

        * **samples** is an array with shape ``(nsamples, ndim)`` and if
          ``sort`` was ``True``, it will also be sorted, and
        * **inds** is an ``(nsamples,)`` list of integer permutations used to
          sort the list of samples.

        Raises a ``RuntimeError`` if the input dimension doesn't match the
        dimension of the kernel.

        """
        t = np.atleast_1d(t)
        if len(t.shape) == 1:
            # Deal with one-dimensional data.
            if sort:
                inds = np.argsort(t)
            else:
                inds = np.arange(len(t), dtype=int)
            t = np.atleast_2d(t).T
        elif sort:
            # Sort the data using a KD-tree.
            inds = nd_sort_samples(t)
        else:
            # Otherwise, assume that the samples are sorted.
            inds = np.arange(t.shape[0], dtype=int)

        # Double check the dimensions against the kernel.
        if len(t.shape) != 2 or t.shape[1] != self.kernel.ndim:
            raise ValueError("Dimension mismatch")

        return t[inds], inds

    def _check_dimensions(self, y):
        n, ndim = self._x.shape
        y = np.atleast_1d(y)
        if len(y.shape) > 1:
            raise ValueError("The predicted dimension must be 1-D")
        if len(y) != n:
            raise ValueError("Dimension mismatch")
        return y

    def compute(self, x, yerr=TINY, sort=True, **kwargs):
        """
        Pre-compute the covariance matrix and factorize it for a set of times
        and uncertainties.

        :param x: ``(nsamples,)`` or ``(nsamples, ndim)``
            The independent coordinates of the data points.

        :param yerr: (optional) ``(nsamples,)`` or scalar
            The Gaussian uncertainties on the data points at coordinates
            ``x``. These values will be added in quadrature to the diagonal of
            the covariance matrix.

        :param sort: (optional)
            Should the samples be sorted before computing the covariance
            matrix? This can lead to more numerically stable results and with
            some linear algebra libraries this can more computationally
            efficient. Either way, this flag is passed directly to
            :func:`parse_samples`. (default: ``True``)

        """
        # Parse the input coordinates.
        self._x, self.inds = self.parse_samples(x, sort)
        try:
            self._yerr = float(yerr) * np.ones(len(x))
        except TypeError:
            self._yerr = self._check_dimensions(yerr)[self.inds]
        self._do_compute(**kwargs)

    def _do_compute(self, _scale=0.5*np.log(2*np.pi)):
        # Compute the kernel matrix.
        K = self.kernel(self._x, self._x)
        K[np.diag_indices_from(K)] += self._yerr ** 2

        # Factor the matrix and compute the log-determinant.
        factor, _ = self._factor = (cholesky(K, overwrite_a=True,
                                             lower=False), False)
        self._const = -(np.sum(np.log(np.diag(factor))) + _scale*len(self._x))

        # Save the computed state.
        self.computed = True

    def recompute(self, quiet=False, **kwargs):
        """
        Re-compute a previously computed model. You might want to do this if
        the kernel parameters change and the kernel is labeled as ``dirty``.

        """
        if not self.computed:
            if not (hasattr(self, "_x") and hasattr(self, "_yerr")):
                raise RuntimeError("You need to compute the model first")
            try:
                # Update the model making sure that we store the original
                # ordering of the points.
                initial_order = np.array(self.inds)
                self.compute(self._x, self._yerr, sort=False, **kwargs)
                self.inds = initial_order
            except (ValueError, LinAlgError):
                if quiet:
                    return False
                raise
        return True

    def _compute_lnlike(self, r):
        return self._const - 0.5*np.dot(r.T, cho_solve(self._factor, r))

    def lnlikelihood(self, y, quiet=False):
        """
        Compute the ln-likelihood of a set of observations under the Gaussian
        process model. You must call ``compute`` before this function.

        :param y: ``(nsamples, )``
            The observations at the coordinates provided in the ``compute``
            step.

        :param quiet:
            If ``True`` return negative infinity instead of raising an
            exception when there is an invalid kernel or linear algebra
            failure. (default: ``False``)

        """
        if not self.recompute(quiet=quiet):
            return -np.inf
        r = self._check_dimensions(y)[self.inds] - self.mean(self._x)
        ll = self._compute_lnlike(r)
        return ll if np.isfinite(ll) else -np.inf

    def grad_lnlikelihood(self, y, dims=None, quiet=False):
        """
        Compute the gradient of the ln-likelihood function as a function of
        the kernel parameters.

        :param y: ``(nsamples,)``
            The list of observations at coordinates ``x`` provided to the
            :func:`compute` function.

        :param dims: (optional)
            If you only want to compute the gradient in some dimensions,
            list them here.

        :param quiet:
            If ``True`` return a gradient of zero instead of raising an
            exception when there is an invalid kernel or linear algebra
            failure. (default: ``False``)

        """
        # By default, compute the gradient in all dimensions.
        if dims is None:
            dims = np.ones(len(self.kernel), dtype=bool)

        # Make sure that the model is computed and try to recompute it if it's
        # dirty.
        if not self.recompute(quiet=quiet):
            return np.zeros_like(dims, dtype=float)

        # Parse the input sample list.
        r = self._check_dimensions(y)[self.inds] - self.mean(self._x)

        # Pre-compute some factors.
        alpha = cho_solve(self._factor, r)
        Kg = self.kernel.grad(self._x, self._x)[dims]

        # Loop over dimensions and compute the gradient in each one.
        g = np.empty(len(Kg))
        for i, k in enumerate(Kg):
            d = sum(map(lambda r: np.dot(alpha, r), alpha[:, None] * k))
            d -= np.sum(np.diag(cho_solve(self._factor, k)))
            g[i] = 0.5 * d

        return g

    def predict(self, y, t):
        """
        Compute the conditional predictive distribution of the model.

        :param y: ``(nsamples,)``
            The observations to condition the model on.

        :param t: ``(ntest,)`` or ``(ntest, ndim)``
            The coordinates where the predictive distribution should be
            computed.

        Returns a tuple ``(mu, cov)`` where

        * **mu** ``(ntest,)`` is the mean of the predictive distribution, and
        * **cov** ``(ntest, ntest)`` is the predictive covariance.

        """
        self.recompute()
        r = self._check_dimensions(y)[self.inds] - self.mean(self._x)
        xs, i = self.parse_samples(t, False)
        alpha = cho_solve(self._factor, r)

        # Compute the predictive mean.
        Kxs = self.kernel(xs, self._x)
        mu = np.dot(Kxs, alpha) + self.mean(xs)

        # Compute the predictive covariance.
        cov = self.kernel(xs, xs)
        cov -= np.dot(Kxs, cho_solve(self._factor, Kxs.T))

        return mu, cov

    def sample_conditional(self, y, t, size=1):
        """
        Draw samples from the predictive conditional distribution.

        :param y: ``(nsamples, )``
            The observations to condition the model on.

        :param t: ``(ntest, )`` or ``(ntest, ndim)``
            The coordinates where the predictive distribution should be
            computed.

        :param size: (optional)
            The number of samples to draw. (default: ``1``)

        Returns **samples** ``(N, ntest)``, a list of predictions at
        coordinates given by ``t``.

        """
        mu, cov = self.predict(y, t)
        return multivariate_gaussian_samples(cov, size, mean=mu)

    def sample(self, t=None, size=1):
        """
        Draw samples from the prior distribution.

        :param t: ``(ntest, )`` or ``(ntest, ndim)`` (optional)
            The coordinates where the model should be sampled. If no
            coordinates are given, the precomputed coordinates and
            factorization are used.

        :param size: (optional)
            The number of samples to draw. (default: ``1``)

        Returns **samples** ``(size, ntest)``, a list of predictions at
        coordinates given by ``t``. If ``size == 1``, the result is a single
        sample with shape ``(ntest,)``.

        """
        if t is None:
            self.recompute()
            n, _ = self._x.shape

            # Generate samples using the precomputed factorization.
            samples = np.dot(np.random.randn(size, n), self._factor[0])
            samples += self.mean(self._x)

            # Reorder the samples correctly.
            results = np.empty_like(samples)
            results[:, self.inds] = samples
            return results[0] if size == 1 else results

        x, _ = self.parse_samples(t, False)
        cov = self.get_matrix(x)
        return multivariate_gaussian_samples(cov, size, mean=self.mean(x))

    def get_matrix(self, t):
        """
        Get the covariance matrix at a given set of independent coordinates.

        :param t: ``(nsamples,)`` or ``(nsamples, ndim)``
            The list of samples.

        """
        r, _ = self.parse_samples(t, False)
        return self.kernel(r, r)

    def optimize(self, x, y, yerr=TINY, sort=True, dims=None, verbose=True,
                 **kwargs):
        """
        A simple and not terribly robust non-linear optimization algorithm for
        the kernel hyperpararmeters.

        :param x: ``(nsamples,)`` or ``(nsamples, ndim)``
            The independent coordinates of the data points.

        :param y: ``(nsamples, )``
            The observations at the coordinates ``x``.

        :param yerr: (optional) ``(nsamples,)`` or scalar
            The Gaussian uncertainties on the data points at coordinates
            ``x``. These values will be added in quadrature to the diagonal of
            the covariance matrix.

        :param sort: (optional)
            Should the samples be sorted before computing the covariance
            matrix?

        :param dims: (optional)
            If you only want to optimize over some parameters, list their
            indices here.

        :param verbose: (optional)
            Display the results of the call to :func:`scipy.optimize.minimize`?
            (default: ``True``)

        Returns ``(pars, results)`` where ``pars`` is the list of optimized
        parameters and ``results`` is the results object returned by
        :func:`scipy.optimize.minimize`.

        """
        self.compute(x, yerr, sort=sort)

        # By default, optimize all the hyperparameters.
        if dims is None:
            dims = np.ones(len(self.kernel), dtype=bool)
        dims = np.arange(len(self.kernel))[dims]

        # Define the objective function and gradient.
        def nll(pars):
            self.kernel[dims] = pars
            ll = self.lnlikelihood(y, quiet=True)
            if not np.isfinite(ll):
                return 1e25  # The optimizers can't deal with infinities.
            return -ll

        def grad_nll(pars):
            self.kernel[dims] = pars
            return -self.grad_lnlikelihood(y, dims=dims, quiet=True)

        # Run the optimization.
        p0 = self.kernel.vector[dims]
        results = op.minimize(nll, p0, jac=grad_nll, **kwargs)

        if verbose:
            print(results.message)

        return self.kernel.vector[dims], results


class _default_mean(object):

    def __init__(self, value):
        self.value = value

    def __call__(self, t):
        return self.value + np.zeros(len(t), dtype=float)

    def __len__(self):
        return 1

    @property
    def vector(self):
        return np.array([self.value])

    @vector.setter
    def vector(self, value):
        self.value = float(value)

    def lnprior(self):
        return 0.0
