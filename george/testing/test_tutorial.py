# -*- coding: utf-8 -*-

from __future__ import division, print_function, absolute_import

__all__ = ["test_tutorial"]

import numpy as np

from .. import kernels
from ..basic import GP


def test_tutorial():
    def model(params, t):
        _, _, amp, loc, sig2 = params
        return amp * np.exp(-0.5 * (t - loc) ** 2 / sig2)

    def lnlike(p, t, y, yerr):
        a, tau = np.exp(p[:2])
        gp = GP(a * kernels.Matern32Kernel(tau) + 0.001)
        gp.compute(t, yerr)
        return gp.lnlikelihood(y - model(p, t))

    def lnprior(p):
        lna, lntau, amp, loc, sig2 = p
        if (-5 < lna < 5 and -5 < lntau < 5 and -10 < amp < 10 and
                -5 < loc < 5 and 0 < sig2 < 3):
            return 0.0
        return -np.inf

    def lnprob(p, x, y, yerr):
        lp = lnprior(p)
        return lp + lnlike(p, x, y, yerr) if np.isfinite(lp) else -np.inf

    np.random.seed(1234)
    x = np.sort(np.random.rand(50))
    yerr = 0.05 + 0.01 * np.random.rand(len(x))
    y = np.sin(x) + yerr * np.random.randn(len(x))
    assert np.isfinite(lnprob([0, 0, -1.0, 0.1, 0.4], x, y, yerr))
