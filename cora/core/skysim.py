# === Start Python 2/3 compatibility
from __future__ import absolute_import, division, print_function, unicode_literals
from future.builtins import *  # noqa  pylint: disable=W0401, W0614
from future.builtins.disabled import *  # noqa  pylint: disable=W0401, W0614

# === End Python 2/3 compatibility

import numpy as np
import scipy.linalg as la
import scipy.integrate as si
import healpy

from cora.util import hputil, nputil


def clarray(aps, lmax, zarray, zromb=3, zwidth=None):
    """Calculate an array of C_l(z, z').

    Parameters
    ----------
    aps : function
        The angular power spectrum to calculate.
    lmax : integer
        Maximum l to calculate up to.
    zarray : array_like
        Array of z's to calculate at.
    zromb : integer
        The Romberg order for integrating over frequency samples.
    zwidth : scalar, optional
        Width of frequency channel to integrate over. If None (default),
        calculate from the separation of the first two bins.

    Returns
    -------
    aps : np.ndarray[lmax+1, len(zarray), len(zarray)]
        Array of the C_l(z,z') values.
    """

    if zromb == 0:
        return aps(
            np.arange(lmax + 1)[:, np.newaxis, np.newaxis],
            zarray[np.newaxis, :, np.newaxis],
            zarray[np.newaxis, np.newaxis, :],
        )

    else:
        zsort = np.sort(zarray)
        zhalf = np.abs(zsort[1] - zsort[0]) / 2.0 if zwidth is None else zwidth / 2.0
        zlen = zarray.size
        zint = 2 ** zromb + 1
        zspace = 2.0 * zhalf / 2 ** zromb

        za = (
            zarray[:, np.newaxis] + np.linspace(-zhalf, zhalf, zint)[np.newaxis, :]
        ).flatten()

        lsections = np.array_split(np.arange(lmax + 1), lmax // 5)

        cla = np.zeros((lmax + 1, zlen, zlen), dtype=np.float64)

        for lsec in lsections:
            clt = aps(
                lsec[:, np.newaxis, np.newaxis],
                za[np.newaxis, :, np.newaxis],
                za[np.newaxis, np.newaxis, :],
            )

            clt = clt.reshape(-1, zlen, zint, zlen, zint)

            clt = si.romb(clt, dx=zspace, axis=4)
            clt = si.romb(clt, dx=zspace, axis=2)

            cla[lsec] = clt / (2 * zhalf) ** 2  # Normalise

        return cla


def mkfullsky(corr, nside, alms=False, gaussvars_list=None):
    """Construct a set of correlated Healpix maps.

    Make a set of full sky gaussian random fields, given the correlation
    structure. Useful for constructing a set of different redshift slices.
    Now also accepts a list of correlation matrices to generate maps from
    with a singe realization.

    Parameters
    ----------
    corr : np.ndarray (lmax+1, numz, numz) or list
        The correlation matrix :math:`C_l(z, z')`, or a list
        of correlation matrices
    nside : integer
        The resolution of the Healpix maps.
    alms : boolean, optional
        If True return the alms instead of the sky maps.
    gaussvars_list : list or array-like
        The complex standard normal variables to be used to generate the
        realization. Default is None which generates them on the fly.
        This is usefull when one needs different maps to be drawn from
        the same realization.

    Returns
    -------
    hpmaps : np.ndarray (numz, npix)
        The Healpix maps. hpmaps[i] is the i'th map.
    """
    if not isinstance(corr, list):
        corr = [corr]
    ncorr = len(corr)

    numz = corr[0].shape[1]
    maxl = corr[0].shape[0] - 1

    if corr[0].shape[2] != numz:
        raise Exception("Correlation matrix is incorrect shape.")

    alm_list = [
        np.zeros((numz, 1, maxl + 1, maxl + 1), dtype=np.complex128)
        for ii in range(ncorr)
    ]

    # Generate gaussian deviates and transform to have correct correlation
    # structure
    for l in range(maxl + 1):
        if gaussvars_list is None:
            gaussvars = nputil.complex_std_normal((numz, l + 1))
        else:
            gaussvars = gaussvars_list[l]
        for ii in range(ncorr):
            # Add in a small diagonal to try and ensure positive definiteness
            cmax = corr[ii][l].diagonal().max() * 1e-14
            corrm = corr[ii][l] + np.identity(numz) * cmax

            trans = nputil.matrix_root_manynull(
                corrm, truncate=False, fixed_ev_sign_convention=True
            )
            alm_list[ii][:, 0, l, : (l + 1)] = np.dot(trans, gaussvars)

    if alms:
        if ncorr == 1:
            return alm_list[0]
        else:
            return alm_list

    sky = []
    for ii in range(ncorr):
        # Perform the spherical harmonic transform for each z
        sky.append(hputil.sphtrans_inv_sky(alm_list[ii], nside))
        sky[ii] = sky[ii][:, 0]

    if ncorr == 1:
        return sky[0]
    else:
        return sky


def mkfullsky_der1(corr, nside, comovd, alms=False, gaussvars_list=None):
    """Construct a set of correlated Healpix maps and their 3D
    spacial derivatives.

    Make a set of full sky gaussian random fields, given the correlation
    structure. Useful for constructing a set of different redshift slices.
    Also accepts a list of correlation matrices to generate maps from
    with a singe realization.

    Parameters
    ----------
    corr : np.ndarray (lmax+1, numz, numz) or list
        The correlation matrix :math:`C_l(z, z')`, or a list
        of correlation matrices
    nside : integer
        The resolution of the Healpix maps.
    alms : boolean, optional
        If True return the alms instead of the sky maps.
    gaussvars_list : list or array-like
        The complex standard normal variables to be used to generate the
        realization. Default is None which generates them on the fly.
        This is usefull when one needs different maps to be drawn from
        the same realization.

    Returns
    -------
    resmaps : list [ncorr]
        The Healpix maps and derivatives.
    """
    if not isinstance(corr, list):
        corr = [corr]
    ncorr = len(corr)

    numz = corr[0].shape[1]
    maxl = corr[0].shape[0] - 1

    if corr[0].shape[2] != numz:
        raise Exception("Correlation matrix is incorrect shape.")

    alm_list = [
        np.zeros((numz, 1, maxl + 1, maxl + 1), dtype=np.complex128)
        for ii in range(ncorr)
    ]

    # Generate gaussian deviates and transform to have correct correlation
    # structure
    for l in range(maxl + 1):
        if gaussvars_list is None:
            gaussvars = nputil.complex_std_normal((numz, l + 1))
        else:
            gaussvars = gaussvars_list[l]
        for ii in range(ncorr):
            # Add in a small diagonal to try and ensure positive definiteness
            cmax = corr[ii][l].diagonal().max() * 1e-14
            corrm = corr[ii][l] + np.identity(numz) * cmax

            trans = nputil.matrix_root_manynull(corrm, truncate=False)
            alm_list[ii][:, 0, l, : (l + 1)] = np.dot(trans, gaussvars)

    if alms:
        if ncorr == 1:
            return alm_list[0]
        else:
            return alm_list

    resmaps = []
    for ii in range(ncorr):
        # Perform the spherical harmonic transform for each z
        sky, d_theta, d_phi = hputil.sphtrans_inv_sky_der1(alm_list[ii], nside)
        # TODO: careful with np.gradient! meaning of second argument (varargs) changed
        # from numpy 1.12 to 1.13. Now it accepts an array of 'x values' along the axis.
        # Before it wanted a scalar representing the 'x-variation' between points.
        # The problem is that if you give it an array in old versions it doesn't crash!
        # I guess it silently takes the first value as 'x-variation'...?
        # spacing = np.gradient(comovd)
        # d_x = np.gradient(sky[:,0],spacing,axis=0)
        d_x = np.gradient(sky[:, 0], comovd, axis=0)
        resmaps.append(
            np.array(
                [
                    sky[:, 0],
                    d_theta[:, 0] / comovd[:, None],
                    d_phi[:, 0] / comovd[:, None],
                    d_x,
                ]
            )
        )

    if ncorr == 1:
        return resmaps[0]
    else:
        return resmaps


def mkconstrained(corr, constraints, nside):
    """Construct a set of Healpix maps, satisfying given constraints
    on specified frequency slices, by using the lowest eigenmodes.

    Parameters
    ----------
    corr : np.ndarray (lmax+1, numz, numz)
        The correlation matrix :math:`C_l(z, z')`.
    constrains : list
        A list of constraints packed as [[frequency_index, healpix map], ...]
    nside : integer
        The resolution of the Healpix maps.

    Returns
    -------
    hpmaps : np.ndarray (numz, npix)
        The Healpix maps. hpmaps[i] is the i'th map.
    """

    numz = corr.shape[1]
    maxl = corr.shape[0] - 1
    larr, marr = healpy.Alm.getlm(maxl)
    matshape = larr.shape + (numz,)

    # The number of constraints
    nmodes = len(constraints)

    # The frequency slices that are constrained.
    f_ind = [c[0] for c in constraints]

    if corr.shape[2] != numz:
        raise Exception("Correlation matrix is incorrect shape.")

    trans = np.zeros((corr.shape[0], nmodes, corr.shape[2]))
    tmat = np.zeros((corr.shape[0], nmodes, nmodes))
    cmap = np.zeros(larr.shape + (nmodes,), dtype=np.complex128)
    cv = np.zeros((numz,) + larr.shape, dtype=np.complex128)

    # Find eigenmodes, extract the largest nmodes (enough to satisfy
    # constraints), and then pull out coefficients for each constrained
    # frequency.
    for i in range(maxl + 1):
        trans[i] = la.eigh(corr[i])[1][:, -nmodes:].T
        tmat[i] = trans[i][:, f_ind]

    # Switch constraint maps into harmonic space
    for i, cons in enumerate(constraints):
        cmap[:, i] = healpy.map2alm(cons[1], lmax=maxl)

    # Solve for the eigenmode amplitudes to satisfy constraints, and project
    # each mode across the whole frequency range.
    for i, l in enumerate(larr):
        if l == 0:
            cv[:, i] = 0.0
        else:
            cv[:, i] = np.dot(trans[l].T, la.solve(tmat[l].T, cmap[i]))

    hpmaps = np.empty((numz, healpy.nside2npix(nside)))

    for i in range(numz):
        hpmaps[i] = healpy.alm2map(cv[i], nside, verbose=False)

    return hpmaps
