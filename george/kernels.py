# -*- coding: utf-8 -*-

from __future__ import division, print_function

__all__ = [
    "Sum", "Product", "Kernel",
    "ConstantKernel", "WhiteKernel", "DotProductKernel",
    "RadialKernel", "ExpKernel", "ExpSquaredKernel", "RBFKernel",
    "CosineKernel", "ExpSine2Kernel",
    "Matern32Kernel", "Matern52Kernel",
]

import numpy as np
from scipy.linalg import cho_factor, cho_solve


class Kernel(object):
    """
    The abstract kernel type. Every kernel implemented in George should be
    a subclass of this object.

    :param pars:
        The hyper-parameters of the kernel.

    :param ndim: (optional)
        The number of input dimensions of the kernel. (default: ``1``)

    """

    is_kernel = True
    kernel_type = -1

    # This function deals with weird behavior when performing arithmetic
    # operations with numpy scalars.
    def __array_wrap__(self, array, context=None):
        if context is None:
            raise TypeError("Invalid operation")
        ufunc, args, _ = context
        if ufunc.__name__ == "multiply":
            return float(args[0]) * args[1]
        elif ufunc.__name__ == "add":
            return float(args[0]) + args[1]
        raise TypeError("Invalid operation")
    __array_priority__ = np.inf

    def __init__(self, *pars, **kwargs):
        self.ndim = kwargs.get("ndim", 1)
        self.pars = np.array(pars)

    def __repr__(self):
        return "{0}({1})".format(self.__class__.__name__,
                                 ", ".join(map("{0}".format,
                                               self.pars) +
                                           ["ndim={0}".format(self.ndim)]))

    def lnprior(self):
        return 0.0

    @property
    def vector(self):
        # return self.pars
        return np.log(self.pars)

    @vector.setter
    def vector(self, v):
        # self.pars = v
        self.pars = np.exp(v)

    @property
    def pars(self):
        """
        A NumPy array listing the vector of hyperparameters specifying the
        kernel.

        """
        return self._pars

    @pars.setter
    def pars(self, v):
        self._pars = np.array(v)
        self.set_pars(v)
        self.dirty = True

    def __getitem__(self, i):
        return self.vector[i]

    def __setitem__(self, i, v):
        vec = self.vector
        vec[i] = v
        self.vector = vec

    def set_pars(self, pars):
        pass

    def __len__(self):
        return len(self.vector)

    def __add__(self, b):
        if not hasattr(b, "is_kernel"):
            return Sum(ConstantKernel(np.sqrt(np.abs(float(b))),
                                      ndim=self.ndim), self)
        return Sum(self, b)

    def __radd__(self, b):
        return self.__add__(b)

    def __mul__(self, b):
        if not hasattr(b, "is_kernel"):
            return Product(ConstantKernel(np.sqrt(np.abs(float(b))),
                                          ndim=self.ndim), self)
        return Product(self, b)

    def __rmul__(self, b):
        return self.__mul__(b)

    def __call__(self, x1, x2):
        """
        The value of the kernel evaluated at a specific pair of coordinates.

        """
        raise NotImplementedError("Must be implemented by subclasses")

    def grad(self, x1, x2):
        """
        The kernel gradient evaluated at a specific pair of coordinates. The
        order of the parameters is the same as the ``.vector`` property.

        """
        raise NotImplementedError("Must be implemented by subclasses")


class _operator(Kernel):
    is_kernel = False
    operator_type = -1

    def __init__(self, k1, k2):
        if k1.ndim != k2.ndim:
            raise ValueError("Dimension mismatch")
        self.k1 = k1
        self.k2 = k2
        self.ndim = k1.ndim
        self.dirty = True

    @property
    def pars(self):
        return np.append(self.k1.pars, self.k2.pars)

    @pars.setter
    def pars(self, v):
        self.dirty = True
        i = len(self.k1)
        self.k1.pars = v[:i]
        self.k2.pars = v[i:]

    @property
    def vector(self):
        return np.append(self.k1.vector, self.k2.vector)

    @vector.setter
    def vector(self, v):
        self.dirty = True
        i = len(self.k1)
        self.k1.vector = v[:i]
        self.k2.vector = v[i:]

    def __getitem__(self, i):
        return self.vector[i]

    def __setitem__(self, i, v):
        p = self.vector
        p[i] = v
        self.vector = p


class Sum(_operator):
    is_kernel = False
    operator_type = 0

    def __repr__(self):
        return "{0} + {1}".format(self.k1, self.k2)

    def __call__(self, x1, x2):
        return self.k1(x1, x2) + self.k2(x1, x2)

    def grad(self, x1, x2):
        g1 = self.k1.grad(x1, x2)
        g2 = self.k2.grad(x1, x2)
        return np.concatenate((g1, g2), axis=0)


class Product(_operator):
    is_kernel = False
    operator_type = 1

    def __repr__(self):
        return "{0} * {1}".format(self.k1, self.k2)

    def __call__(self, x1, x2):
        return self.k1(x1, x2) * self.k2(x1, x2)

    def grad(self, x1, x2):
        g1 = self.k1.grad(x1, x2)
        g2 = self.k2.grad(x1, x2)
        s = [None] + [slice(None)] * (len(g1.shape) - 1)
        return np.concatenate((g1 * self.k2(x1, x2)[s],
                               g2 * self.k1(x1, x2)[s]), axis=0)


class ConstantKernel(Kernel):
    r"""
    This kernel returns the constant

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = c^2

    where :math:`c` is a parameter.

    :param value:
        The constant value :math:`c` in the above equation.

    """
    kernel_type = 0

    def __init__(self, value, ndim=1):
        super(ConstantKernel, self).__init__(value, ndim=ndim)

    def __call__(self, x1, x2):
        return self.pars[0] ** 2 + np.zeros((len(x1), len(x2)))

    def grad(self, x1, x2):
        return 2 * self.pars[0] ** 2 + np.zeros((1, len(x1), len(x2)))


class WhiteKernel(Kernel):
    r"""
    This kernel returns constant along the diagonal.

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = c^2 \, \delta_{ij}

    where :math:`c` is the parameter.

    :param value:
        The constant value :math:`c` in the above equation.

    """
    kernel_type = 8

    def __init__(self, value, ndim=1):
        super(WhiteKernel, self).__init__(value, ndim=ndim)

    def __call__(self, x1, x2):
        d = np.sum((x1[:, None] - x2[None, :]) ** 2, axis=-1)
        return self.pars[0] ** 2 * (d == 0.0)

    def grad(self, x1, x2):
        d = np.sum((x1[:, None] - x2[None, :]) ** 2, axis=-1)
        g = np.zeros(np.append(1, d.shape))
        g[0] = 2 * self.pars[0] ** 2 * (d == 0)
        return g


class DotProductKernel(Kernel):
    r"""
    The dot-product kernel takes the form

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = \mathbf{x}_i^{\mathrm{T}} \cdot
                                         \mathbf{x}_j

    """
    kernel_type = 1

    def __init__(self, ndim=1):
        super(DotProductKernel, self).__init__(ndim=ndim)

    def __call__(self, x1, x2):
        return np.sum(x1[:, None] * x2[None, :], axis=-1)

    def grad(self, x1, x2):
        return np.zeros(np.append(1, self(x1, x2).shape))


class RadialKernel(Kernel):
    r"""
    This abstract base class implements a radial kernel in an arbitrary
    metric.  The metric is specified as a matrix :math:`C` where the
    radius :math:`{r_{ij}}^2` is

    .. math::

        {r_{ij}}^2 = (\mathbf{x}_i - \mathbf{x}_j)^\mathrm{T}\,
                     C^{-1}\,(\mathbf{x}_i - \mathbf{x}_j)

    :param metric:
        There are a few different ways that you can specify the metric:

        1. if ``metric`` is a scalar, the metric is assumed isotropic with an
           axis-aligned variance of ``metric`` in each dimension,
        2. if ``metric`` has the same length as ``ndim``, it is assumed to
           specify the axis-aligned variances in each dimension, and
        3. if ``metric`` has the length ``(ndim*ndim + ndim) / 2``, it is
           assumed to give the triangular matrix :math:`C` in the form
           specified below.

    :param isotropic: (optional)
        Is the metric isotropic? If not given, this is inferred from the
        metric.

    :param axis_aligned: (optional)
        Is the metric axis-aligned? If not given, this is inferred from the
        metric.

    However you specify ``metric``, the ``pars`` property of the kernel will
    be a NumPy array with elements:

    .. code-block:: python

        kernel.pars = [a, b, c, d, e, f, ...]

    where

    .. math::

        C = \left ( \begin{array}{cccc}
            a & b & d & \\
            b & c & e & \cdots \\
            d & e & f & \\
             & \vdots & & \ddots
        \end{array} \right )

    **Note:**
    Subclasses should implement the :func:`get_value` method to give
    the value of the kernel at a given radius and this class will deal with
    the metric.

    **Another Note:**
    1-D is treated as a special case for speed.

    """

    def __init__(self, metric, ndim=1, extra=[], axis_aligned=None,
                 isotropic=None):
        # Special case 1-D for speed.
        if ndim == 1:
            self.nextra = len(extra)
            super(RadialKernel, self).__init__(*(np.append(extra, metric)),
                                               ndim=1)
        else:
            # Save the meta-data about the kernel parameters.
            self.axis_aligned = axis_aligned
            self.isotropic = isotropic
            self.nextra = len(extra)
            self.ndim = ndim
            pars = self._coerce_metric(metric)
            super(RadialKernel, self).__init__(*(np.append(extra, pars)),
                                               ndim=ndim)

            # Build the gradient indicator masks.
            self.gm = np.empty(np.append(len(self)-self.nextra,
                                         self.matrix.shape),
                               dtype=int)
            for i in range(len(self) - self.nextra):
                ind = np.zeros(len(self) - self.nextra, dtype=int)
                ind[i] = 1
                self.gm[i] = self._build_matrix(self._coerce_metric(ind))

    def _coerce_metric(self, metric):
        # Coerce the input metric into the form required by the C++ solver.
        inds = np.tri(self.ndim, dtype=bool)
        if np.isscalar(metric) or len(metric) == 1:
            # A scalar metric gives an isotropic metric.
            pars = np.diag(float(metric) * np.ones(self.ndim))[inds]

            # Update the isotropic flag if it wasn't specified.
            if self.isotropic is None:
                self.isotropic = True
        else:
            # If we asked for an isotropic kernel but the given metric
            # isn't a scalar then something went wrong.
            if self.isotropic:
                raise ValueError("Isotropic kernels only take one metric "
                                 "parameter, {0}".format(metric))

            # Deal with 1- or 2-dimensional metrics.
            metric = np.atleast_1d(metric)
            if len(metric) == self.ndim:
                pars = np.diag(metric)[inds]

                # Update the axis_aligned flag if it wasn't specified.
                if self.axis_aligned is None:
                    self.axis_aligned = True
            elif len(metric) == self.ndim * (self.ndim + 1) // 2:
                if self.axis_aligned:
                    raise ValueError("Axis-aligned kernels can only "
                                     "take a diagonal metric")
                pars = np.array(metric)
            else:
                raise ValueError("Dimension mismatch")
        return pars

    @property
    def vector(self):
        if self.ndim == 1:
            return np.log(self.pars)
        if self.isotropic:
            return np.log(np.append(self.pars[:self.nextra],
                                    self.matrix[0, 0]))
        if self.axis_aligned:
            p = self.matrix[np.diag_indices_from(self.matrix)]
            return np.log(np.append(self.pars[:self.nextra], p))
        return np.log(self.pars)

    @vector.setter
    def vector(self, v):
        if self.ndim == 1:
            self.pars = np.exp(v)
            return
        p = self._coerce_metric(np.exp(v[self.nextra:]))
        self.pars = np.append(np.exp(v[:self.nextra]), p)

    def _build_matrix(self, x, dtype=float):
        m = np.zeros((self.ndim, self.ndim), dtype=dtype)
        m[np.tril_indices_from(m)] = x
        m += m.T
        m[np.diag_indices_from(m)] *= 0.5
        return m

    def set_pars(self, pars):
        if self.ndim > 1:
            self.matrix = self._build_matrix(pars[self.nextra:])
            self._factor = cho_factor(self.matrix)
        else:
            self.ivar = 1.0 / pars[self.nextra]

    def __call__(self, x1, x2):
        if self.ndim > 1:
            dx = x1[:, None] - x2[None, :]
            dxf = dx.reshape((-1, self.ndim)).T
            r = np.sum(dxf * cho_solve(self._factor, dxf), axis=0)
            r = r.reshape(dx.shape[:-1])
        else:
            r = np.sum((x1[:, None] - x2[None, :]), axis=-1) ** 2 * self.ivar
        return self.get_value(r)

    def grad(self, x1, x2):
        if self.ndim > 1:
            dx = x1[:, None] - x2[None, :]
            dxf = dx.reshape((-1, self.ndim)).T
            alpha = cho_solve(self._factor, dxf)

            # Compute the radial gradient.
            g = np.empty(np.append(len(self.gm)+self.nextra, dx.shape[:-1]))
            for i, gm in enumerate(self.gm):
                g[self.nextra+i] = \
                    np.sum(dxf*cho_solve(self._factor, np.dot(gm, alpha)),
                           axis=0).reshape(dx.shape[:-1])

            # Compute the function gradient.
            r = np.sum(dxf * alpha, axis=0)
            r = r.reshape(dx.shape[:-1])
            if self.nextra:
                kg, g[:self.nextra] = self.get_grad(r)
            else:
                kg = self.get_grad(r)

            # Update the full gradient to include the kernel.
            s1 = [None] + [slice(None)] * len(r.shape)
            s2 = [slice(None)] + [None] * len(r.shape)
            g[self.nextra:] *= -kg[s1] * np.exp(self.vector[self.nextra:])[s2]

        else:
            r = np.sum((x1[:, None] - x2[None, :]), axis=-1) ** 2 * self.ivar

            # Compute the radial gradient.
            g = np.empty(np.append(len(self), r.shape))
            if self.nextra:
                kg, g[:self.nextra] = self.get_grad(r)
            else:
                kg = self.get_grad(r)
            g[self.nextra] = -kg * r

        return g

    def get_value(self, r):
        raise NotImplementedError("Subclasses must implement this method.")

    def get_grad(self, r):
        raise NotImplementedError("Subclasses must implement this method.")


class ExpKernel(RadialKernel):
    r"""
    The exponential kernel is a :class:`RadialKernel` where the value at a
    given radius :math:`r^2` is given by:

    .. math::

        k({r_{ij}}) = \exp \left ( -|r| \right )

    :param metric:
        The custom metric specified as described in the :class:`RadialKernel`
        description.

    """
    kernel_type = 2

    def get_value(self, dx):
        return np.exp(-np.sqrt(dx))

    def get_grad(self, dx):
        sx = np.sqrt(dx)
        m = sx > 0
        g = np.zeros_like(sx)
        g[m] = -0.5 * np.exp(-sx[m]) / sx[m]
        return g


class ExpSquaredKernel(RadialKernel):
    r"""
    The exponential-squared kernel is a :class:`RadialKernel` where the value
    at a given radius :math:`r^2` is given by:

    .. math::

        k(r^2) = \exp \left ( -\frac{r^2}{2} \right )

    :param metric:
        The custom metric specified as described in the :class:`RadialKernel`
        description.

    """
    kernel_type = 3

    def get_value(self, dx):
        return np.exp(-0.5 * dx)

    def get_grad(self, dx):
        return -0.5 * np.exp(-0.5 * dx)


class RBFKernel(ExpSquaredKernel):
    r"""
    An alias for :class:`ExpSquaredKernel`.

    """


class Matern32Kernel(RadialKernel):
    r"""
    The Matern-3/2 kernel is a :class:`RadialKernel` where the value at a
    given radius :math:`r^2` is given by:

    .. math::

        k(r^2) = \left( 1+\sqrt{3\,r^2} \right)\,
                 \exp \left (-\sqrt{3\,r^2} \right )

    :param metric:
        The custom metric specified as described in the :class:`RadialKernel`
        description.

    """
    kernel_type = 6

    def get_value(self, dx):
        r = np.sqrt(3.0 * dx)
        return (1.0 + r) * np.exp(-r)

    def get_grad(self, dx):
        r = np.sqrt(3.0 * dx)
        return -3 * 0.5 * np.exp(-r)


class Matern52Kernel(RadialKernel):
    r"""
    The Matern-5/2 kernel is a :class:`RadialKernel` where the value at a
    given radius :math:`r^2` is given by:

    .. math::

        k(r^2) = \left( 1+\sqrt{5\,r^2} + \frac{5\,r^2}{3} \right)\,
                 \exp \left (-\sqrt{5\,r^2} \right )

    :param metric:
        The custom metric specified as described in the :class:`RadialKernel`
        description.

    """
    kernel_type = 7

    def get_value(self, dx):
        r = np.sqrt(5.0 * dx)
        return (1.0 + r + r*r / 3.0) * np.exp(-r)

    def get_grad(self, dx):
        r = np.sqrt(5.0 * dx)
        return -5 * (1.0 + r) * np.exp(-r) / 6.0


class RationalQuadraticKernel(RadialKernel):
    r"""
    The Matern-5/2 kernel is a :class:`RadialKernel` where the value at a
    given radius :math:`r^2` is given by:

    .. math::

        k(r^2) = \left( 1+ \frac{r^2}{2\,\alpha} \right )^{-\alpha}

    :param alpha:
        The shape parameter :math:`\alpha`.

    :param metric:
        The custom metric specified as described in the :class:`RadialKernel`
        description.

    """
    kernel_type = 9

    def __init__(self, alpha, metric, ndim=1, **kwargs):
        super(RationalQuadraticKernel, self).__init__(metric, extra=[alpha],
                                                      ndim=ndim, **kwargs)

    def get_value(self, dx):
        a = self.pars[0]
        return (1.0 + 0.5 * dx / a) ** (-a)

    def get_grad(self, dx):
        a = self.pars[0]
        t1 = 1 + 0.5 * dx / a
        t2 = 2 * a + dx
        return -0.5 * t1**(-a-1), t1 ** (-a) * (dx - t2 * np.log(t1)) / t2 * a


class CosineKernel(Kernel):
    r"""
    The cosine kernel is given by:

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) =
            \cos\left(\frac{2\,\pi}{P}\,\left|x_i-x_j\right| \right)

    where :math:`P` is the period.

    :param period:
        The period :math:`P` of the oscillation (in the same units as
        :math:`\mathbf{x}`).

    **Note:**
    A shortcoming of this kernel is that it currently only accepts a single
    period so it's not very applicable to problems with input dimension larger
    than one.

    """
    kernel_type = 4

    def __init__(self, period, ndim=1):
        super(CosineKernel, self).__init__(period, ndim=ndim)

    def set_pars(self, pars, twopi=2*np.pi):
        self._omega = twopi / float(pars)

    def __call__(self, x1, x2):
        return np.cos(self._omega * np.sqrt(np.sum((x1[:, None]
                                                    - x2[None, :]) ** 2,
                                                   axis=-1)))

    def grad(self, x1, x2, itwopi=1.0/(2*np.pi)):
        x = np.sqrt(np.sum((x1[:, None] - x2[None, :]) ** 2, axis=-1))
        g = np.empty(np.append(1, x.shape))
        s = [0] + [slice(None)] * len(x.shape)
        g[s] = x * np.sin(self._omega * x) * self._omega
        return g


class ExpSine2Kernel(Kernel):
    r"""
    The exp-sine-squared kernel is used to model stellar rotation and *might*
    be applicable in some other contexts. It is given by the equation:

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) =
            \sin \left( -\Gamma\,\sin^2\left[
                \frac{\pi}{P}\,\left|x_i-x_j\right|
            \right] \right)

    where :math:`\Gamma` is the "scale" of the correlation and :math:`P` is
    the period of the oscillation measured in the same units as
    :math:`\mathbf{x}`.

    :param gamma:
        The scale :math:`\Gamma` of the correlations.

    :param period:
        The period :math:`P` of the oscillation (in the same units as
        :math:`\mathbf{x}`).

    **Note:**
    A shortcoming of this kernel is that it currently only accepts a single
    period and scale so it's not very applicable to problems with input
    dimension larger than one.

    """
    kernel_type = 5

    def __init__(self, gamma, period, ndim=1):
        super(ExpSine2Kernel, self).__init__(gamma, period, ndim=ndim)

    def set_pars(self, pars):
        self._omega = np.pi / pars[1]

    def __call__(self, x1, x2):
        d = x1[:, None] - x2[None, :]
        s = np.sin(self._omega * np.sqrt(np.sum(d ** 2, axis=-1)))
        return np.exp(-self.pars[0] * s**2)

    def grad(self, x1, x2):
        # Pre-compute some factors.
        d = x1[:, None] - x2[None, :]
        x = np.sqrt(np.sum(d ** 2, axis=-1))
        sx = np.sin(self._omega * x)
        cx = np.cos(self._omega * x)
        A2 = sx*sx
        a = self.pars[0]
        f = np.exp(-a * A2)

        # Build the output array.
        g = np.empty(np.append(2, x.shape))
        s = [0] + [slice(None)] * len(x.shape)

        # Compute the scale derivative.
        g[s] = -f * A2 * self.pars[0]

        # Compute the period derivative.
        s[0] = 1
        g[s] = 2 * f * a * sx * cx * x * self._omega

        return g
