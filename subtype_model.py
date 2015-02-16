import logging
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.stats as stats

from collections import namedtuple, OrderedDict
from patsy import dmatrix
from scipy.linalg import solve
from scipy.misc import logsumexp

from .logistic_regression import LogisticRegression


Trajectory = namedtuple('Trajectory', ['key', 't', 'y', 'covariates'])


def make_trajectories(key, time, marker, dataframe):
    trajectories = []

    for k, data in dataframe.groupby(key):
        t = data[time].values.ravel()
        y = data[marker].values.ravel()
        covariates = np.ones(1)

        idx = np.argsort(t)
        trj = Trajectory(k, t[idx], y[idx], covariates)
        trajectories.append(trj)

    return trajectories


def add_covariates(trajectories, key, formula, dataframe):
    cov_matrix = dmatrix(formula, dataframe)
    row_keys = dataframe[key]
    covariates = {k:v for k, v in zip(row_keys, cov_matrix)}

    with_covariates = []

    for trj in trajectories:
        trj_copy = Trajectory(
            trj.key, trj.t, trj.y, covariates[trj.key])
        with_covariates.append(trj_copy)

    return with_covariates


def truncate_trajectory(trajectory, num_obs=None, censoring_time=None):
    if num_obs is not None:
        t = trajectory.t[:num_obs]
        y = trajectory.y[:num_obs]

    elif censoring_time is not None:
        keep = trajectory.t < censoring_time
        t = trajectory.t[keep]
        y = trajectory.y[keep]

    else:
        raise RuntimeError('You must specify num_obs or censoring_time.')

    new_trj = Trajectory(trajectory.key, t, y, trajectory.covariates)

    return new_trj


def predictive_contexts(trajectory):
    n = len(trajectory.t)

    for num_obs in range(1, n):
        obs_trj = truncate_trajectory(trajectory, num_obs)
        t_new = trajectory.t[num_obs:]
        y_new = trajectory.y[num_obs:]
        
        yield obs_trj, t_new, y_new


class ConditionalPredictor:
    def __init__(self, model, trajectory):
        self.model = model
        self.trajectory = trajectory

    def predict(self, t_new):
        y_new = self.model.predict(t_new, self.trajectory)
        return y_new

    def plot(self, ax=None, *args, **kwargs):
        lower = self.model.basis.lower
        upper = self.model.basis.upper
        t_grid = np.linspace(lower, upper, 100)
        y_grid = self.predict(t_grid)

        if ax is None:
            fig, ax = plt.subplots(*args, **kwargs)

        else:
            fig = ax.figure
            
        ax.plot(self.trajectory.t, self.trajectory.y, 'xb', label='Observed')
        ax.plot(t_grid, y_grid, '-r', label='Predicted')

        return fig, ax


class SubtypeMixture:
    def __init__(self, nsubtypes, ncovariates, basis_fn, cov_fn):
        self.nsubtypes = nsubtypes
        self.ncovariates = ncovariates
        self.basis = basis_fn
        self.cov = cov_fn
        self._init_params()

    def _init_params(self):
        self.subtype_marginal = LogisticRegression(
            self.nsubtypes, self.ncovariates)
        self.coef = np.zeros((self.nsubtypes, self.basis.df))

    def loglik(self, trajectory, z, X=None, C=None):
        X = self.basis(trajectory.t) if X is None else X
        C = self.cov(trajectory.t) if C is None else C
        m = X.dot(self.coef[z])
        y = trajectory.y.ravel()
        covariates = np.atleast_2d(trajectory.covariates)
        qz = self.subtype_marginal.predict_prob(covariates).ravel()

        lpz = np.log(qz[z])
        lpy = stats.multivariate_normal.logpdf(y, m, C)

        return lpz + lpy

    def posterior(self, trajectory):
        all_loglik = [self.loglik(trajectory, z) for z in range(self.nsubtypes)]
        marg_loglik = logsumexp(all_loglik)
        qz = np.exp(all_loglik - marg_loglik)
        return qz

    def predict(self, t_new, trajectory):
        X_new = self.basis(t_new)
        X_obs = self.basis(trajectory.t)
        y_obs = trajectory.y
        C1 = self.cov(t_new, trajectory.t)
        C2 = self.cov(trajectory.t)
        qz = self.posterior(trajectory)
        y_new = np.zeros_like(t_new)

        for z, w in enumerate(qz):
            m1 = X_new.dot(self.coef[z]).ravel()
            m2 = X_obs.dot(self.coef[z]).ravel()
            y_new += w * (m1 + C1.dot(solve(C2, y_obs - m2)).ravel())

        return y_new

    def conditional(self, trajectory):
        return ConditionalPredictor(self, trajectory)

    TrajectoryData = namedtuple(
        'TrajectoryData', ['X', 'C', 'cov_xx', 'cov_xy'])

    def fit(self, trajectories, max_iter=100, tol=1e-5):

        ## Cache computations needed for estimation and store
        ## covariates of all trajectories in a single feature matrix.

        cache = OrderedDict()
        covariates = []

        for trj in trajectories:
            X = self.basis(trj.t)
            C = self.cov(trj.t)

            cov_xx = X.T.dot(solve(C, X))
            cov_xy = X.T.dot(solve(C, trj.y)).ravel()

            cache[trj.key] = self.TrajectoryData(X, C, cov_xx, cov_xy)
            
            covariates.append(trj.covariates)

        covariates = np.vstack(covariates)
        qz = stats.dirichlet.rvs(self.nsubtypes * [0.1], len(trajectories))

        ## EM iterations start here.

        iteration = 0
        logl = np.zeros(max_iter + 1)
        logl[iteration] = -float('inf')

        while True:
            iteration += 1

            ## M-step (1): Estimate subtype probabilities.

            self.subtype_marginal.fit(covariates, qz)

            ## M-step (2): Estimate subtype coefficients.

            cov_xx = np.zeros((self.nsubtypes, self.basis.df, self.basis.df))
            cov_xy = np.zeros((self.nsubtypes, self.basis.df))

            for i, trj in enumerate(trajectories):
                for z, w in enumerate(qz[i]):
                    cov_xx[z] += w * cache[trj.key].cov_xx
                    cov_xy[z] += w * cache[trj.key].cov_xy

            for z in range(self.nsubtypes):
                self.coef[z] = solve(cov_xx[z], cov_xy[z])

            ## E-step: Recompute log-likelihood and posteriors

            for i, trj in enumerate(trajectories):
                X = cache[trj.key].X
                C = cache[trj.key].C
                stypes = range(self.nsubtypes)
                all_loglik = [self.loglik(trj, z, X, C) for z in stypes]
                marg_loglik = logsumexp(all_loglik)
                qz[i] = np.exp(all_loglik - marg_loglik)
                logl[iteration] += marg_loglik

            delta = logl[iteration] - logl[iteration - 1]
            abs_delta = delta / abs(logl[iteration])

            msg = 'Iter={:03d}, LL={:.02f}, Convergence={:.06f}'
            msg = msg.format(iteration, logl[iteration], abs_delta)
            logging.info(msg)

            if iteration >= max_iter or abs_delta < tol:
                break

        return self