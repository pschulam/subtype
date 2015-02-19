import matplotlib.pyplot as plt

from patsy import dmatrix

from .subtype_model import Trajectory


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